'use client'

/**
 * The browser half of the export parity check — M5's closing condition.
 *
 * *"You export a 1080p 9:16 MP4 and it looks exactly like the preview."* That
 * is a visual claim, and until something measures it, it is a hope. This page
 * puts known colours through **the real preview grade** — the exported
 * `FRAGMENT_SRC` from `renderer.ts` and the real `parseCubeLut` — and hands the
 * result back on `window`. `e2e/lut-parity.mjs` puts the same colours through
 * the real export graph and compares the two.
 *
 * **Why swatches rather than a video frame.** The two implementations differ in
 * exactly one place that can drift: the LUT, its interpolation, and the mix at
 * `strength`. Everything else an export does — trim, concat, scale, pad — is
 * geometry both sides do identically and which a pixel comparison would only
 * add noise to. Solid colours isolate the grade and make a difference of one
 * level visible, where a photograph would hide it in the texture.
 *
 * **Why it is a page and not a unit test.** The preview grade runs on a GPU
 * through GLSL. Reimplementing it in TypeScript to compare against FFmpeg would
 * compare a reimplementation against FFmpeg and prove nothing about what users
 * see. This is the shader itself, in a browser, on a real context.
 *
 * A `/spike` route for the same reason M1's compositor was: throwaway
 * verification code that is not part of the product.
 */

import { useEffect, useRef, useState } from 'react'

import { parseCubeLut, type CubeLut } from '@/editor/playback/cube'
import { FRAGMENT_SRC } from '@/editor/playback/renderer'

/** The colours put through the grade. Spread across the cube rather than
 *  clustered: a LUT can be right in the greys and wrong in the saturated
 *  corners, which is exactly where a colour-space mistake shows. */
export const SWATCHES: readonly [number, number, number][] = [
  [0, 0, 0],
  [255, 255, 255],
  [128, 128, 128],
  [58, 110, 165],
  [200, 60, 40],
  [40, 180, 90],
  [240, 200, 30],
  [120, 40, 160],
]

const VERTEX_SRC = `#version 300 es
in vec2 aPos;
out vec2 vUv;
void main() {
  vUv = aPos;
  gl_Position = vec4(aPos * 2.0 - 1.0, 0.0, 1.0);
}
`

declare global {
  interface Window {
    __lutParity?: {
      lut: string
      strength: number
      swatches: readonly [number, number, number][]
      /** Graded RGB, one per swatch, in the same order. */
      graded: [number, number, number][]
      error?: string
    }
  }
}

function compile(gl: WebGL2RenderingContext, type: number, src: string): WebGLShader {
  const shader = gl.createShader(type)
  if (!shader) throw new Error('could not create a shader')
  gl.shaderSource(shader, src)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error(gl.getShaderInfoLog(shader) ?? 'shader would not compile')
  }
  return shader
}

/** One swatch through the real fragment shader, read back as bytes. */
function gradeSwatches(
  gl: WebGL2RenderingContext,
  lut: CubeLut,
  strength: number,
): [number, number, number][] {
  const program = gl.createProgram()
  if (!program) throw new Error('could not create a program')
  gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX_SRC))
  gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SRC))
  gl.linkProgram(program)
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(program) ?? 'program would not link')
  }
  gl.useProgram(program)

  const buffer = gl.createBuffer()
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), gl.STATIC_DRAW)
  const aPos = gl.getAttribLocation(program, 'aPos')
  gl.enableVertexAttribArray(aPos)
  gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0)

  // The LUT, uploaded exactly as the preview uploads it — `parseCubeLut` packs
  // to RGBA bytes ready for `texImage3D` without a copy, and the filtering is
  // what makes the browser's interpolation match FFmpeg's trilinear one.
  const lutTexture = gl.createTexture()
  gl.activeTexture(gl.TEXTURE2)
  gl.bindTexture(gl.TEXTURE_3D, lutTexture)
  gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
  gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
  gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
  gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
  gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_R, gl.CLAMP_TO_EDGE)
  gl.texImage3D(
    gl.TEXTURE_3D, 0, gl.RGBA8, lut.size, lut.size, lut.size, 0, gl.RGBA,
    gl.UNSIGNED_BYTE, lut.rgba,
  )

  const source = gl.createTexture()
  gl.activeTexture(gl.TEXTURE0)
  gl.bindTexture(gl.TEXTURE_2D, source)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)

  gl.uniform1i(gl.getUniformLocation(program, 'uTexA'), 0)
  gl.uniform1i(gl.getUniformLocation(program, 'uTexB'), 1)
  gl.uniform1i(gl.getUniformLocation(program, 'uLut'), 2)
  // The whole canvas is the layer: no letterboxing, so nothing but the grade
  // is being measured.
  gl.uniform4f(gl.getUniformLocation(program, 'uFitA'), 1, 1, 0, 0)
  gl.uniform4f(gl.getUniformLocation(program, 'uFitB'), 1, 1, 0, 0)
  gl.uniform2f(gl.getUniformLocation(program, 'uHas'), 1, 0)
  gl.uniform2f(gl.getUniformLocation(program, 'uStrength'), strength, 0)
  gl.uniform1f(gl.getUniformLocation(program, 'uMix'), 0)
  gl.uniform1f(gl.getUniformLocation(program, 'uLutSize'), lut.size)
  gl.uniform3f(gl.getUniformLocation(program, 'uBackground'), 0, 0, 0)

  const out: [number, number, number][] = []
  const pixel = new Uint8Array(4)
  for (const [r, g, b] of SWATCHES) {
    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, source)
    gl.texImage2D(
      gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
      new Uint8Array([r, g, b, 255]),
    )
    gl.viewport(0, 0, 1, 1)
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
    gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel)
    out.push([pixel[0] ?? 0, pixel[1] ?? 0, pixel[2] ?? 0])
  }
  return out
}

export function ParityHarness() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [state, setState] = useState('starting')

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const lutName = params.get('lut') ?? 'cyberpunk'
    const strength = Number(params.get('strength') ?? '1')

    const run = async () => {
      const canvas = canvasRef.current
      // Never return silently. An early return here leaves `__lutParity`
      // unset, and the driver reports "timed out waiting for the harness"
      // instead of the reason — which is exactly what happened the first time
      // this ran.
      if (!canvas) throw new Error('the canvas never mounted')
      canvas.width = 1
      canvas.height = 1
      // `preserveDrawingBuffer` so `readPixels` is defined after the draw
      // returns; the preview does not need it and deliberately does not set it.
      const gl = canvas.getContext('webgl2', { preserveDrawingBuffer: true })
      if (!gl) throw new Error('no WebGL2 context')

      const response = await fetch(`/luts/${lutName}.cube`)
      if (!response.ok) throw new Error(`could not fetch ${lutName}.cube`)
      const lut = parseCubeLut(await response.text())

      window.__lutParity = {
        lut: lutName,
        strength,
        swatches: SWATCHES,
        graded: gradeSwatches(gl, lut, strength),
      }
      setState('done')
    }

    run().catch((error: unknown) => {
      window.__lutParity = {
        lut: lutName,
        strength,
        swatches: SWATCHES,
        graded: [],
        error: (error as Error).message,
      }
      setState('error')
    })
  }, [])

  return (
    <main className="p-6 text-sm">
      <h1 className="font-semibold">LUT parity harness</h1>
      <p style={{ color: 'var(--color-ink-2)' }}>
        Drives the real preview shader over known colours. Read by{' '}
        <code>e2e/lut-parity.mjs</code>.
      </p>
      <canvas ref={canvasRef} style={{ width: 8, height: 8 }} />
      <p data-testid="parity-state">{state}</p>
    </main>
  )
}
