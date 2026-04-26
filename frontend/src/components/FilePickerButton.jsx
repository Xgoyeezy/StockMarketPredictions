import { useRef } from 'react'
import Button from './Button'
import { NativeInput } from './ControlPrimitives'

export default function FilePickerButton({
  children,
  accept = '',
  onFileSelect,
  variant = 'ghost',
  size = 'sm',
  className = '',
  ...props
}) {
  const inputRef = useRef(null)

  return (
    <>
      <Button
        {...props}
        type="button"
        variant={variant}
        size={size}
        className={className}
        onClick={() => inputRef.current?.click()}
      >
        {children}
      </Button>
      <NativeInput
        ref={inputRef}
        type="file"
        accept={accept}
        hidden
        onChange={onFileSelect}
      />
    </>
  )
}
