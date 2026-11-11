export const OFFICE_BRAND = {
  id: "qubit-office",
  name: "Qubit Office",
  shortName: "Office",
  title: "Qubit Office",
  description:
    "Qubit Office — your agents' office: who is working, who needs you, and what was delivered.",
  logoPath: "/favicon.svg",
  credit: "Office art: LimeZu — https://limezu.itch.io",
  creditUrl: "https://limezu.itch.io",
  legalLabel: "Qubit Office includes modified Habblaud under the MIT License.",
  legalUrl: "/plugin-apps/qubit-office/habblaud/LICENSE",
  upstream: "https://github.com/marmottajr/habblaud",
  upstreamCommit: "4ccce2537472fe5a8d432f06974494d2e778ff15",
} as const;

export type OfficeBrand = typeof OFFICE_BRAND;
