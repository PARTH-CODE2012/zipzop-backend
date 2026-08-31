/**
 * The WebGL2 half of the compositor: two video textures, a 3D LUT, one draw.
 *
 * Scope is exactly what M1 has to prove — sample the frame under the playhead,
 * grade it, and blend it with a second clip. Crop, transform and text are not
 * here: crop and transform are a matrix in the vertex shader and carry no
 * unknowns, and text is a 2D canvas layered on top (see `text-overlay.ts`).
 *
 * Two decisions that are easy to get wrong and expensive to debug:
 *
 *  - `UNPACK_FLIP_Y_WEBGL` is never enabled. WebGL2 rejects `texImage3D` while
 *    it is set, so uploading the LUT would fail with INVALID_OPERATION the
 *    moment a video upload turned it on. The V flip lives in the shader.
 *  - The LUT sample coordinate is scaled by `(N-1)/N` and offset by `0.5/N`.
 *    Sampling the raw 0–1 range instead lands half a texel outside the table at
 *    both ends, which crushes the darkest and brightest values — visible as
 *    clipped shadows that look like a bad grade rather than a bad lookup.
 */

import type { CubeLut } from './cube'

const VERTEX_SRC = `#version 300 es
in vec2 aPos;
out vec2 vUv;

void main() {
  vUv = aPos;
  gl_Position = vec4(aPos * 2.0 - 1.0, 0.0, 1.0);
}
`

/**
 * Exported so the export-parity harness can run the *real* shader rather than a
 * copy of it. A second copy of this string is a second grade, and the whole
 * point of the parity check is that there is exactly one — see
 * `e2e/lut-parity.mjs`.
 */
export const FRAGMENT_SRC = `#version 300 es
precision highp float;
precision highp sampler3D;

in vec2 vUv;
out vec4 fragColor;

uniform sampler2D uTexA;
uniform sampler2D uTexB;
uniform sampler3D uLut;

// xy = size of the drawn rect as a fraction of the canvas, zw = its origin.
uniform vec4 uFitA;
uniform vec4 uFitB;
uniform vec2 uHas;        // 1.0 when the layer has a frame to show
uniform vec2 uStrength;   // per-clip grade strength, 0-1
uniform float uMix;       // 0 = base, 1 = over
uniform float uLutSize;
uniform vec3 uBackground;

vec3 graded(vec3 c, float strength) {
  float scale = (uLutSize - 1.0) / uLutSize;
  float offset = 0.5 / uLutSize;
  vec3 looked = texture(uLut, clamp(c, 0.0, 1.0) * scale + offset).rgb;
  return mix(c, looked, clamp(strength, 0.0, 1.0));
}

// Branch-free on purpose: an implicit-LOD texture fetch inside non-uniform
// control flow is undefined in GLSL ES 3.00, and letterboxing is per-pixel.
vec3 layer(sampler2D tex, vec4 fit, float has, float strength) {
  vec2 uv = (vUv - fit.zw) / fit.xy;
  vec2 inside = step(vec2(0.0), uv) * step(uv, vec2(1.0));
  vec2 safe = clamp(uv, 0.0, 1.0);
  vec3 c = graded(texture(tex, vec2(safe.x, 1.0 - safe.y)).rgb, strength);
  return mix(uBackground, c, inside.x * inside.y * has);
}

void main() {
  vec3 a = layer(uTexA, uFitA, uHas.x, uStrength.x);
  vec3 b = layer(uTexB, uFitB, uHas.y, uStrength.y);
  fragColor = vec4(mix(a, b, clamp(uMix, 0.0, 1.0)), 1.0);
}
`

const UNIFORM_NAMES = [
  'uTexA',
  'uTexB',
  'uLut',
  'uFitA',
  'uFitB',
  'uHas',
  'uStrength',
  'uMix',
  'uLutSize',
  'uBackground',
] as const

type UniformName = (typeof UNIFORM_NAMES)[number]

export interface LayerInput {
  readonly video: HTMLVideoElement
  /** 0–1. Already multiplied by the global slider. */
  readonly strength: number
}

export interface DrawInput {
  readonly base: LayerInput | null
  readonly over: LayerInput | null
  readonly mix: number
}

export class RendererError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'RendererError'
  }
}

interface Slot {
  texture: WebGLTexture | null
  /**
   * Which element's pixels are in this texture right now.
   *
   * Slots are assigned by *role* — base and over — so the base slot changes
   * element at every cut. Without this, "the slot already has a frame" would
   * be read as "this clip has a frame", and the outgoing clip's last picture
   * would be drawn under the incoming clip's name.
   */
  source: HTMLVideoElement | null
  width: number
  height: number
  /** `currentTime` of the last frame uploaded, so a paused clip uploads once. */
  uploadedAt: number
}

/** Letterbox: fit the source inside the canvas without cropping it. */
export function fitContain(
  srcW: number,
  srcH: number,
  dstW: number,
  dstH: number,
): [w: number, h: number, x: number, y: number] {
  if (srcW <= 0 || srcH <= 0 || dstW <= 0 || dstH <= 0) return [1, 1, 0, 0]
  const src = srcW / srcH
  const dst = dstW / dstH
  const w = src > dst ? 1 : src / dst
  const h = src > dst ? dst / src : 1
  return [w, h, (1 - w) / 2, (1 - h) / 2]
}

export class CompositorRenderer {
  private readonly canvas: HTMLCanvasElement
  private gl: WebGL2RenderingContext | null = null
  private program: WebGLProgram | null = null
  private vao: WebGLVertexArrayObject | null = null
  private quad: WebGLBuffer | null = null
  private lutTexture: WebGLTexture | null = null
  private uniforms = new Map<UniformName, WebGLUniformLocation | null>()

  private readonly slots: [Slot, Slot] = [
    { texture: null, source: null, width: 0, height: 0, uploadedAt: Number.NaN },
    { texture: null, source: null, width: 0, height: 0, uploadedAt: Number.NaN },
  ]

  /** Kept so the LUT can be re-uploaded after a context loss. */
  private lut: CubeLut | null = null

  private contextLost = false
  private disposed = false
  private rendererName: string | null = null

  private readonly onLost: (event: Event) => void
  private readonly onRestored: () => void

  constructor(
    canvas: HTMLCanvasElement,
    private readonly onContextEvent?: (state: 'lost' | 'restored') => void,
  ) {
    this.canvas = canvas

    this.onLost = (event: Event) => {
      // Without preventDefault the browser will not fire `restored`, and the
      // canvas stays black for the rest of the session.
      event.preventDefault()
      this.contextLost = true
      this.releaseResourceHandles()
      this.onContextEvent?.('lost')
    }

    this.onRestored = () => {
      if (this.disposed) return
      this.contextLost = false
      try {
        this.createResources()
        if (this.lut !== null) this.uploadLut(this.lut)
        this.onContextEvent?.('restored')
      } catch {
        // A restore that cannot rebuild is indistinguishable from a lost
        // context as far as callers are concerned.
        this.contextLost = true
      }
    }

    canvas.addEventListener('webglcontextlost', this.onLost, false)
    canvas.addEventListener('webglcontextrestored', this.onRestored, false)

    const gl = canvas.getContext('webgl2', {
      alpha: false,
      depth: false,
      stencil: false,
      antialias: false,
      premultipliedAlpha: false,
      preserveDrawingBuffer: false,
      powerPreference: 'high-performance',
    })
    if (gl === null) {
      throw new RendererError(
        'WebGL2 is not available in this browser. The compositor has no fallback — see docs/04-frontend-architecture.md §4.',
      )
    }
    this.gl = gl
    this.createResources()
  }

  get lost(): boolean {
    return this.contextLost
  }

  /** Cached: `getParameter` is a synchronous round-trip, and the HUD asks often. */
  get renderer(): string {
    if (this.rendererName !== null) return this.rendererName
    const gl = this.gl
    if (gl === null || this.contextLost) return 'unknown'
    const info = gl.getExtension('WEBGL_debug_renderer_info')
    const name =
      info === null
        ? (gl.getParameter(gl.VERSION) as string)
        : (gl.getParameter(info.UNMASKED_RENDERER_WEBGL) as string)
    this.rendererName = name
    return name
  }

  /** Only used by the HUD button that proves the recovery path works. */
  simulateContextLoss(): boolean {
    const ext = this.gl?.getExtension('WEBGL_lose_context') ?? null
    if (ext === null) return false
    ext.loseContext()
    window.setTimeout(() => ext.restoreContext(), 900)
    return true
  }

  setLut(lut: CubeLut): void {
    this.lut = lut
    if (!this.contextLost) this.uploadLut(lut)
  }

  resize(width: number, height: number): void {
    if (this.canvas.width !== width) this.canvas.width = width
    if (this.canvas.height !== height) this.canvas.height = height
  }

  /**
   * `skipped` means the canvas was left exactly as it was.
   *
   * That is deliberate and it is the difference between a clean cut and a
   * flash. When a clip is under the playhead but its element has not presented
   * a frame yet, the right thing to show is the picture that is already there
   * — not black. Black is only correct for a genuine gap, where `base` is null
   * and there is nothing to show by definition.
   */
  draw(input: DrawInput): 'drawn' | 'skipped' {
    const gl = this.gl
    if (gl === null || this.contextLost || this.program === null) return 'skipped'

    if (!this.hasPixels(input.base, 0) || !this.hasPixels(input.over, 1)) return 'skipped'

    gl.viewport(0, 0, this.canvas.width, this.canvas.height)
    gl.clearColor(0, 0, 0, 1)
    gl.clear(gl.COLOR_BUFFER_BIT)

    gl.useProgram(this.program)
    gl.bindVertexArray(this.vao)

    const base = this.bindLayer(0, input.base)
    const over = this.bindLayer(1, input.over)

    gl.activeTexture(gl.TEXTURE2)
    gl.bindTexture(gl.TEXTURE_3D, this.lutTexture)

    gl.uniform1i(this.uniform('uTexA'), 0)
    gl.uniform1i(this.uniform('uTexB'), 1)
    gl.uniform1i(this.uniform('uLut'), 2)
    gl.uniform4f(this.uniform('uFitA'), base.fit[0], base.fit[1], base.fit[2], base.fit[3])
    gl.uniform4f(this.uniform('uFitB'), over.fit[0], over.fit[1], over.fit[2], over.fit[3])
    gl.uniform2f(this.uniform('uHas'), base.has, over.has)
    gl.uniform2f(
      this.uniform('uStrength'),
      input.base?.strength ?? 0,
      input.over?.strength ?? 0,
    )
    gl.uniform1f(this.uniform('uMix'), over.has > 0 ? input.mix : 0)
    gl.uniform1f(this.uniform('uLutSize'), this.lut?.size ?? 2)
    gl.uniform3f(this.uniform('uBackground'), 0, 0, 0)

    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
    gl.bindVertexArray(null)
    return 'drawn'
  }

  /** Either the element can be sampled now, or its own last frame is still in the slot. */
  private hasPixels(layer: LayerInput | null, index: 0 | 1): boolean {
    if (layer === null) return true
    const video = layer.video
    if (video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0) return true
    const slot = this.slots[index]
    return slot.source === video && slot.width > 0
  }

  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    this.canvas.removeEventListener('webglcontextlost', this.onLost, false)
    this.canvas.removeEventListener('webglcontextrestored', this.onRestored, false)

    const gl = this.gl
    if (gl !== null && !this.contextLost) {
      for (const slot of this.slots) if (slot.texture !== null) gl.deleteTexture(slot.texture)
      if (this.lutTexture !== null) gl.deleteTexture(this.lutTexture)
      if (this.quad !== null) gl.deleteBuffer(this.quad)
      if (this.vao !== null) gl.deleteVertexArray(this.vao)
      if (this.program !== null) gl.deleteProgram(this.program)
    }
    this.releaseResourceHandles()
    this.gl = null
  }

  // ------------------------------------------------------------------ internals

  private uniform(name: UniformName): WebGLUniformLocation | null {
    return this.uniforms.get(name) ?? null
  }

  private releaseResourceHandles(): void {
    this.program = null
    this.vao = null
    this.quad = null
    this.lutTexture = null
    this.rendererName = null
    this.uniforms.clear()
    for (const slot of this.slots) {
      slot.texture = null
      slot.source = null
      slot.width = 0
      slot.height = 0
      slot.uploadedAt = Number.NaN
    }
  }

  private createResources(): void {
    const gl = this.gl
    if (gl === null) throw new RendererError('no GL context')

    // Never enabled: it makes texImage3D fail, and the shader flips V instead.
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false)
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false)
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1)
    gl.disable(gl.DEPTH_TEST)
    gl.disable(gl.BLEND)

    const program = linkProgram(gl, VERTEX_SRC, FRAGMENT_SRC)
    this.program = program

    this.uniforms.clear()
    for (const name of UNIFORM_NAMES) {
      this.uniforms.set(name, gl.getUniformLocation(program, name))
    }

    const vao = gl.createVertexArray()
    const quad = gl.createBuffer()
    if (vao === null || quad === null) throw new RendererError('could not allocate the quad')

    gl.bindVertexArray(vao)
    gl.bindBuffer(gl.ARRAY_BUFFER, quad)
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]),
      gl.STATIC_DRAW,
    )
    const aPos = gl.getAttribLocation(program, 'aPos')
    gl.enableVertexAttribArray(aPos)
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0)
    gl.bindVertexArray(null)

    this.vao = vao
    this.quad = quad

    for (const slot of this.slots) {
      slot.texture = createTexture2D(gl)
      slot.source = null
      slot.width = 0
      slot.height = 0
      slot.uploadedAt = Number.NaN
    }
  }

  private uploadLut(lut: CubeLut): void {
    const gl = this.gl
    if (gl === null) return

    if (this.lutTexture === null) {
      const tex = gl.createTexture()
      if (tex === null) throw new RendererError('could not allocate the LUT texture')
      this.lutTexture = tex
    }

    gl.activeTexture(gl.TEXTURE2)
    gl.bindTexture(gl.TEXTURE_3D, this.lutTexture)
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1)
    gl.texImage3D(
      gl.TEXTURE_3D,
      0,
      gl.RGBA8,
      lut.size,
      lut.size,
      lut.size,
      0,
      gl.RGBA,
      gl.UNSIGNED_BYTE,
      lut.rgba,
    )
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_R, gl.CLAMP_TO_EDGE)
  }

  private bindLayer(
    index: 0 | 1,
    layer: LayerInput | null,
  ): { fit: [number, number, number, number]; has: number } {
    const gl = this.gl
    const slot = this.slots[index]
    if (gl === null) return { fit: [1, 1, 0, 0], has: 0 }

    gl.activeTexture(index === 0 ? gl.TEXTURE0 : gl.TEXTURE1)
    gl.bindTexture(gl.TEXTURE_2D, slot.texture)

    if (layer === null) return { fit: [1, 1, 0, 0], has: 0 }

    const video = layer.video
    const w = video.videoWidth
    const h = video.videoHeight

    // HAVE_CURRENT_DATA. Uploading before that throws on some drivers and
    // uploads a black frame on others. Mid-seek the element briefly drops back
    // below it, so keep showing whatever was uploaded last rather than
    // blanking — that flash is precisely what M1 is meant to rule out.
    if (w === 0 || h === 0 || video.readyState < 2) {
      if (slot.source !== video || slot.width === 0) return { fit: [1, 1, 0, 0], has: 0 }
      return {
        fit: fitContain(slot.width, slot.height, this.canvas.width, this.canvas.height),
        has: 1,
      }
    }

    // A new element in this slot always forces a fresh upload: `uploadedAt`
    // compares `currentTime`, and two different clips can sit at the same
    // timestamp, which would skip the upload and draw the wrong picture.
    const reused = slot.source === video
    if (!reused || slot.width !== w || slot.height !== h) {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video)
      slot.source = video
      slot.width = w
      slot.height = h
      slot.uploadedAt = video.currentTime
    } else if (slot.uploadedAt !== video.currentTime) {
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, gl.RGBA, gl.UNSIGNED_BYTE, video)
      slot.uploadedAt = video.currentTime
    }

    return {
      fit: fitContain(w, h, this.canvas.width, this.canvas.height),
      has: 1,
    }
  }
}

function createTexture2D(gl: WebGL2RenderingContext): WebGLTexture {
  const tex = gl.createTexture()
  if (tex === null) throw new RendererError('could not allocate a video texture')
  gl.bindTexture(gl.TEXTURE_2D, tex)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
  return tex
}

function compileShader(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type)
  if (shader === null) throw new RendererError('could not create a shader')
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader) ?? 'no log'
    gl.deleteShader(shader)
    throw new RendererError(`shader failed to compile: ${log}`)
  }
  return shader
}

function linkProgram(gl: WebGL2RenderingContext, vs: string, fs: string): WebGLProgram {
  const vertex = compileShader(gl, gl.VERTEX_SHADER, vs)
  const fragment = compileShader(gl, gl.FRAGMENT_SHADER, fs)
  const program = gl.createProgram()
  if (program === null) throw new RendererError('could not create a program')

  gl.attachShader(program, vertex)
  gl.attachShader(program, fragment)
  gl.linkProgram(program)

  // Attached shaders are kept alive by the program until it is deleted.
  gl.deleteShader(vertex)
  gl.deleteShader(fragment)

  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const log = gl.getProgramInfoLog(program) ?? 'no log'
    gl.deleteProgram(program)
    throw new RendererError(`program failed to link: ${log}`)
  }
  return program
}
