// pixelfx core boundary — the SUBSET vendored for Qubit Office weather.
//
// Upstream's src/core/index.js exports the whole library (actors, rigs, acts,
// scenes, chains). The weather effects only reach the modules below, so this
// file is the explicit dependency boundary: copy nothing else, import nothing
// else. See ../PROVENANCE.md.

export * from "./math.js";
export * from "./color.js";
export { P, Particles } from "./sim.js";
export { clearFrame, composite, createFrame, createFx } from "./fx.js";
