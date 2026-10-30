// Legacy import path. The canonical desktop types live in `./qubit`; this
// module re-exports them so older imports keep one structural identity (two
// copies of SessionMessage made every cross-import an assignment error).
export * from './qubit'

export type { QubitConfig as HermesConfig, QubitConfigRecord as HermesConfigRecord } from './qubit'
