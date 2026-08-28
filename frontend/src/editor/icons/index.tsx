/**
 * The icon set — charter §10, *"Tabler outline, and only outline"*.
 *
 * Hand-drawn rather than pulled from `@tabler/icons-react` or a webfont CDN: the
 * charter names about twenty marks, all of them simple line shapes, and a
 * dependency pulling in thousands of icons to use twenty is the wrong trade for
 * an editor that already has a 500-clip performance budget to protect.
 *
 * Every icon is `stroke="currentColor"` and nothing else — no `fill`, no
 * literal colour. That is what makes rule 3 (*"no state is carried by hue
 * alone"*) free: an icon inherits whatever colour its button is already using
 * for the fill, ring or weight change, so the icon changes with the state
 * without a second thing to keep in sync.
 *
 * `1.5` stroke width and `round` joins throughout, matching the charter's
 * "nominal stroke 1.5px". Default size 15 — "inline"; pass `size={17}` for a
 * tool tile, `20` is the charter's stated maximum.
 */

import type { SVGProps } from 'react'

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'stroke' | 'fill' | 'viewBox'> {
  size?: number
}

function Icon({ size = 15, children, ...rest }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...rest}
    >
      {children}
    </svg>
  )
}

// --------------------------------------------------------------------------
// Free editing tools
// --------------------------------------------------------------------------

export function IconPointer(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 3l14 8.5-6 1.2-3 6.3-5-16z" />
    </Icon>
  )
}

export function IconScissors(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="6.5" cy="6.5" r="2.5" />
      <circle cx="6.5" cy="17.5" r="2.5" />
      <path d="M8.5 8.2 20 20M20 4 8.5 15.8" />
    </Icon>
  )
}

export function IconTrim(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 12h5M15 12h5M9 7l-3 5 3 5M15 7l3 5-3 5" />
    </Icon>
  )
}

export function IconCopy(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M6 15H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v1" />
    </Icon>
  )
}

export function IconTrash(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 7h16M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2m-9 0 1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" />
      <path d="M10 11v6M14 11v6" />
    </Icon>
  )
}

export function IconTypography(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M5 5h14M12 5v14M8 19h8" />
    </Icon>
  )
}

export function IconVolume(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 9h3l5-4v14l-5-4H4z" />
      <path d="M16 9a4 4 0 0 1 0 6" />
    </Icon>
  )
}

export function IconVolumeOff(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 9h3l5-4v14l-5-4H4z" />
      <path d="M16 9l5 6M21 9l-5 6" />
    </Icon>
  )
}

export function IconCrop(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 2v14a2 2 0 0 0 2 2h14M18 22V8a2 2 0 0 0-2-2H2" />
    </Icon>
  )
}

export function IconTransition(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 7h11M14 7l-3-3M14 7l-3 3" />
      <path d="M21 17H10M10 17l3-3M10 17l3 3" />
    </Icon>
  )
}

// --------------------------------------------------------------------------
// History
// --------------------------------------------------------------------------

export function IconUndo(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M7 10H4V6" />
      <path d="M4 10a8 8 0 1 1 2.3 6" />
    </Icon>
  )
}

export function IconRedo(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M17 10h3V6" />
      <path d="M20 10a8 8 0 1 0-2.3 6" />
    </Icon>
  )
}

// --------------------------------------------------------------------------
// Transport
// --------------------------------------------------------------------------

export function IconPlay(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 4.5v15l13-7.5z" strokeLinejoin="round" />
    </Icon>
  )
}

export function IconPause(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M7 4.5h3.5v15H7zM13.5 4.5H17v15h-3.5z" />
    </Icon>
  )
}

// --------------------------------------------------------------------------
// Media
// --------------------------------------------------------------------------

export function IconPhoto(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <circle cx="8.5" cy="9.5" r="1.5" />
      <path d="m4 17 5-5 3 3 5-6 3 3" />
    </Icon>
  )
}

export function IconMovie(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M7 5v14M17 5v14M3 10h4M17 10h4M3 15h4M17 15h4" />
    </Icon>
  )
}

export function IconMusic(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="6.5" cy="17.5" r="2.5" />
      <circle cx="17.5" cy="15.5" r="2.5" />
      <path d="M9 17.5V5.5l11-2v12" />
    </Icon>
  )
}

export function IconUpload(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
      <path d="M7 9l5-5 5 5M12 4v12" />
    </Icon>
  )
}

// --------------------------------------------------------------------------
// AI — every one of these carries the sparkle. Charter §11: it is the mark
// that means "this costs credits", and it appears nowhere else.
// --------------------------------------------------------------------------

export function IconSparkles(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 3l1.8 4.9L18.5 9l-4.7 1.9L12 15l-1.8-4.1L5.5 9l4.7-1.1z" strokeLinejoin="round" />
      <path d="M19 15l.8 2.1L22 18l-2.2.9L19 21l-.8-2.1L16 18l2.2-.9z" strokeLinejoin="round" />
    </Icon>
  )
}

export function IconMessage(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 5h16v11H8l-4 4z" strokeLinejoin="round" />
    </Icon>
  )
}

export function IconWand(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 20l10-10M17 3l1 2 2 1-2 1-1 2-1-2-2-1 2-1z" strokeLinejoin="round" />
      <path d="M13 13l2 2" />
    </Icon>
  )
}

export function IconPalette(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 3a9 8 0 1 0 0 16c1.1 0 2-.8 2-2 0-.5-.2-1-.5-1.3-.3-.4-.5-.8-.5-1.2 0-.8.7-1.5 1.5-1.5H16a5 5 0 0 0 5-5c0-2.8-2.1-5-5-5" />
      <circle cx="7.5" cy="10.5" r="1" fill="currentColor" stroke="none" />
      <circle cx="10.5" cy="7" r="1" fill="currentColor" stroke="none" />
      <circle cx="15" cy="7.5" r="1" fill="currentColor" stroke="none" />
    </Icon>
  )
}

// --------------------------------------------------------------------------
// Chrome
// --------------------------------------------------------------------------

export function IconCoins(props: IconProps) {
  return (
    <Icon {...props}>
      <ellipse cx="9" cy="7" rx="5" ry="3" />
      <path d="M4 7v6c0 1.7 2.2 3 5 3s5-1.3 5-3V7" />
      <path d="M13 9.2c2.9.2 5 1.5 5 3v6c0 1.7-2.2 3-5 3-2.5 0-4.6-1-5-2.3" />
    </Icon>
  )
}

export function IconMagnet(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 4v7a6 6 0 0 0 12 0V4" />
      <path d="M6 4h4v7a2 2 0 0 1-4 0zM14 4h4v7a2 2 0 0 1-4 0z" />
    </Icon>
  )
}

export function IconLogout(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5M21 12H9" />
    </Icon>
  )
}

// --------------------------------------------------------------------------
// Transport — M4.5 item 2
//
// The transport moved out of the application header and under the picture, so
// it needs the marks a transport is expected to have. Frame-step and
// jump-to-end already existed as keyboard shortcuts with no visible control,
// which is the half of that item nobody could discover.
// --------------------------------------------------------------------------

export function IconSkipStart(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M18 5.5v13L8 12z" strokeLinejoin="round" />
      <path d="M6 5v14" />
    </Icon>
  )
}

export function IconSkipEnd(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 5.5v13L16 12z" strokeLinejoin="round" />
      <path d="M18 5v14" />
    </Icon>
  )
}

export function IconStepBack(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M17 6.5v11L9 12z" strokeLinejoin="round" />
      <path d="M7 6.5v11" />
    </Icon>
  )
}

export function IconStepForward(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M7 6.5v11L15 12z" strokeLinejoin="round" />
      <path d="M17 6.5v11" />
    </Icon>
  )
}

// --------------------------------------------------------------------------
// Mode rail — M4.5 item 4
//
// One icon per mode, in the same outline language as everything above. The
// rail grows by one of these per tool phase 2 adds, which is the property the
// stacked panels did not have.
// --------------------------------------------------------------------------

export function IconLibrary(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h3A1.5 1.5 0 0 1 10 5.5v13A1.5 1.5 0 0 1 8.5 20h-3A1.5 1.5 0 0 1 4 18.5z" />
      <path d="M13 5.5A1.5 1.5 0 0 1 14.5 4h1A1.5 1.5 0 0 1 17 5.5v13a1.5 1.5 0 0 1-1.5 1.5h-1a1.5 1.5 0 0 1-1.5-1.5z" />
      <path d="M19.5 6.5l1.4 12" />
    </Icon>
  )
}

export function IconSliders(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 8h10M18 8h2M4 16h4M12 16h8" />
      <circle cx="16" cy="8" r="2" />
      <circle cx="10" cy="16" r="2" />
    </Icon>
  )
}

export function IconInfo(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5M12 8h.01" />
    </Icon>
  )
}
