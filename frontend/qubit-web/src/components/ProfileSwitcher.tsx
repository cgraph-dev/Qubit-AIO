import { useMemo } from "react";
import { Users } from "lucide-react";
import { Select } from "@/sakuga/ui";
import { useProfileScope } from "@/contexts/useProfileScope";
import { useI18n } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The machine dashboard's single write-target selector.
 *
 * Rendered in the sidebar above the nav. Every management page (Config,
 * Keys, Skills, MCP, Models) reads/writes the selected profile via the
 * fetchJSON ?profile= injection. Hidden when only one profile exists.
 */
export function ProfileSwitcher({ collapsed }: ProfileSwitcherProps) {
  const { profile, currentProfile, profiles, setProfile } = useProfileScope();
  const { t } = useI18n();

  const currentDashboardLabel = useMemo(
    () =>
      (t.app.currentProfileOption ?? "this dashboard ({name})").replace(
        "{name}",
        currentProfile || "default",
      ),
    [currentProfile, t.app.currentProfileOption],
  );

  if (profiles.length < 2) return null;

  const managed = profile || currentProfile || "default";
  const isOther = !!profile && profile !== currentProfile;
  const managingLabel = t.app.managingProfile ?? "Managing profile";

  return (
    <div
      className={cn(
        "flex items-center gap-2 border-b border-current/10 px-3 py-2",
        collapsed && "lg:justify-center lg:px-0",
      )}
      title={managingLabel}
    >
      <Users
        className={cn(
          "h-3.5 w-3.5 shrink-0",
          isOther ? "text-amber-300" : "text-text-tertiary",
        )}
      />

      <Select
        label={managingLabel}
        className={cn("qsk-field min-w-0 flex-1", collapsed && "lg:hidden")}
        id="qubit-profile-switcher"
        onChange={setProfile}
        value={profile}
        options={[
          { value: "", label: currentDashboardLabel },
          ...profiles
            .filter((name) => name !== currentProfile)
            .map((name) => ({ value: name, label: name })),
        ]}
      />

      {collapsed && <span className="sr-only">{managed}</span>}
    </div>
  );
}

interface ProfileSwitcherProps {
  collapsed?: boolean;
}
