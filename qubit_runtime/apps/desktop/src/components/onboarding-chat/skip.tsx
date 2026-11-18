import { useStore } from '@nanostores/react'
import { Button } from '@/components/ui/button'

import { $chatOnboardingSolo, skipChatOnboarding } from '@/components/onboarding-chat/assembly'

export function OnboardingSkip() {
  const solo = useStore($chatOnboardingSolo)

  if (!solo) {
    return null
  }

  return (
    <Button
      className="ml-auto text-[11px] text-(--ui-text-quaternary) transition-colors hover:text-(--ui-text-secondary)"
      onClick={skipChatOnboarding}
      type="button"
    >
      Skip setup
    </Button>
  )
}
