I checked the Codex session history directly, not just the documentation.
Conclusion: both projects may help Qubit later, but neither will fix the current Hermes-parity problems automatically.
- OpenMuse is useful for durable tasks: progress tracking, pause/resume/retry, approvals, receipts, browser/terminal workers, and artifact handling. Borrow those patterns inside Qubit’s existing gateway and session store. Do not embed its server as a second agent authority. OpenMuse
- HarnessRouter is more relevant to model/backend routing. It could become an optional adapter allowing Qubit to route between Brain4, API models, or other harnesses. Do not replace Qubit’s gateway or create a second session/database authority. HarnessRouter
The session history confirms our previous decisions:
- Bonsai should be an optional qubit-brainv0.1 profile, benchmarked against Brain4—not an automatic replacement.
- /chat should remain the Hermes-style terminal using API/high-context models.
- /qubit-chat should use the local model for routing and voice, with escalation when necessary.
- OpenViking already exists in the Qubit source as an optional memory plugin, but activation and end-to-end behavior still need proof.
- Jev currently exists as an evaluation/routing plan, disabled by default—not as a production reasoning upgrade.
Recommended order:
1. Finish and verify Qubit’s core parity and gateway reliability.
2. Benchmark Brain4 versus Bonsai on the same tasks and hardware.
3. Add OpenMuse-inspired task progress, approvals, retries, and receipts using Qubit’s existing stores.
4. Test HarnessRouter separately as an optional adapter.
5. Enable and verify OpenViking.
6. Enable Jev only as an advisory router after the baseline works.