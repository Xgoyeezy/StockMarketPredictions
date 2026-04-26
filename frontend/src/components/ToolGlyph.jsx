import { joinClasses } from './ControlPrimitives'

function glyphFor(tool) {
  const common = {
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.5,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
  }

  switch (tool) {
    case 'pan':
      return (
        <>
          <path {...common} d="M8 2.5v11" />
          <path {...common} d="M2.5 8h11" />
          <path {...common} d="M8 2.5 6.4 4.1" />
          <path {...common} d="M8 2.5 9.6 4.1" />
          <path {...common} d="M8 13.5 6.4 11.9" />
          <path {...common} d="M8 13.5 9.6 11.9" />
          <path {...common} d="M2.5 8 4.1 6.4" />
          <path {...common} d="M2.5 8 4.1 9.6" />
          <path {...common} d="M13.5 8 11.9 6.4" />
          <path {...common} d="M13.5 8 11.9 9.6" />
        </>
      )
    case 'crosshair':
      return (
        <>
          <circle {...common} cx="8" cy="8" r="3.2" />
          <path {...common} d="M8 1.8v2.2" />
          <path {...common} d="M8 12v2.2" />
          <path {...common} d="M1.8 8h2.2" />
          <path {...common} d="M12 8h2.2" />
        </>
      )
    case 'hline':
      return <path {...common} d="M2.5 8h11" />
    case 'trend':
      return (
        <>
          <path {...common} d="M3 11.8 11.8 3" />
          <path {...common} d="M9.6 3h2.2v2.2" />
        </>
      )
    case 'rectangle':
      return <rect {...common} x="3" y="4" width="10" height="8" rx="1.5" />
    case 'note':
      return (
        <>
          <rect {...common} x="3.2" y="2.8" width="9.6" height="10.4" rx="1.6" />
          <path {...common} d="M5.4 6.2h5.2" />
          <path {...common} d="M5.4 8.6h5.2" />
          <path {...common} d="M5.4 11h3.2" />
        </>
      )
    case 'ray':
      return (
        <>
          <path {...common} d="M3 12.8 11.8 4" />
          <path {...common} d="M9.6 4h2.2v2.2" />
        </>
      )
    case 'measure':
      return (
        <>
          <path {...common} d="M3 11.5h10" />
          <path {...common} d="M4.5 9.8v3.4" />
          <path {...common} d="M7.5 10.5v2.7" />
          <path {...common} d="M10.5 9.8v3.4" />
        </>
      )
    case 'erase':
      return (
        <>
          <path {...common} d="M5.2 11.8 2.8 9.4a1 1 0 0 1 0-1.4l4.3-4.3a1 1 0 0 1 1.4 0l4.7 4.7a1 1 0 0 1 0 1.4l-2 2" />
          <path {...common} d="M6.4 13.2h6.6" />
        </>
      )
    default:
      return <circle {...common} cx="8" cy="8" r="3.4" />
  }
}

export default function ToolGlyph({ glyph, className = '', ...props }) {
  return (
    <svg
      {...props}
      viewBox="0 0 16 16"
      className={joinClasses('ui-tool-glyph', className)}
      aria-hidden="true"
    >
      {glyphFor(glyph)}
    </svg>
  )
}
