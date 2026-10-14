/* Vite handles CSS side-effect imports; TypeScript needs to be told they are
   legal modules, or `tsc`/the editor reports every `import "x.css"` as a
   missing module. Declared here rather than per-file so a new stylesheet needs
   no additional setup. */
declare module "*.css";
