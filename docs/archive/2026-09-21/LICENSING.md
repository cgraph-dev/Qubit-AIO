# Licensing and upstream-reuse policy

Qubit Voice Agent is a school project with a layered license boundary:

- `LICENSE` applies only to original Qubit material.
- Third-party and upstream-derived material keeps its original license.
- `THIRD_PARTY_NOTICES.md` and `docs/UPSTREAM_REUSE.md` are part of the
  licensing record and must ship with any permitted educational copy.

The repository is not an OSI-approved open-source project as a whole. The
educational license restricts redistribution and commercial use of original
Qubit material. It cannot override rights granted by MIT, Apache-2.0,
BSD-3-Clause, GPL, or any other applicable third-party license.

## Rules for agents and contributors

Before copying or adapting code, an agent must:

1. Identify whether the code is original, third-party, generated, or a
   dependency.
2. Record the upstream repository, exact commit or release, source file and
   symbol, license, and local modifications in `docs/UPSTREAM_REUSE.md`.
3. Preserve required copyright and license notices in
   `THIRD_PARTY_NOTICES.md` and in distributed source where required.
4. Copy the smallest compatible implementation and its relevant conformance
   test; do not copy an entire framework for one behavior.
5. Stop and report `BLOCKED — LICENSE REVIEW REQUIRED` if the license is
   missing, unknown, incompatible, or the proposed use exceeds its terms.
6. Never claim that upstream code is covered by `LICENSE`, and never replace
   an upstream license with the school-project restrictions.
7. Never add copied code, fixtures, credentials, private data, or model files
   to a release artifact unless its provenance and distribution rights are
   recorded and verified.

## Distribution rule

Before a wheel, source archive, school submission, or public release is made,
the agent must run the repository's license/provenance checks and inspect the
artifact contents. Original Qubit code must retain the restrictions in
`LICENSE`; third-party files must retain their applicable notices. A school
submission must not be described as “open source” merely because it contains
open-source dependencies.

This policy is project guidance, not legal advice. Ask the copyright holder or
an appropriate school/legal advisor before redistributing upstream code or
commercializing the project.
