import { cn } from '@/lib/utils'
import { useTheme } from '@/themes'

const assetPath = (path: string) => `${import.meta.env.BASE_URL}${path.replace(/^\/+/, '')}`

// Brand badge: the Qubit mark in the app-icon squircle, theme-aware — light
// mode shows the mark on the light squircle, dark mode on the #0d1117 one.
// The mark PNG carries the rounded shape and transparent corners, so no
// tile/rounding classes here. Size via className (default size-14).
//
// This is the only brand mark the app draws, so it must be Qubit's own artwork:
// a user who sees one logo in the window and another in the About box has been
// told the app is two different products.
export function BrandMark({ className, ...props }: React.ComponentProps<'span'>) {
  const { renderedMode } = useTheme()
  const dark = renderedMode === 'dark'

  return (
    <span className={cn('inline-flex size-14 shrink-0 items-center justify-center', className)} {...props}>
      <img alt="" className="size-full object-contain" src={assetPath('qubit.png')} />
    </span>
  )
}
