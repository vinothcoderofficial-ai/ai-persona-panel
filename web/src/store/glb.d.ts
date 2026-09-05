/**
 * Vite turns `import url from "…/x.glb?url"` into a string: the dev server
 * serves the file through `/@fs/`, and `vite build` copies it into
 * `dist/assets/` with a content hash and rewrites the string. TypeScript knows
 * none of that on its own.
 *
 * Vite ships this declaration in `vite/client`, but `web/tsconfig.json` has no
 * `types` entry and adding one would pull in every other ambient type Vite
 * declares. Declaring the one form this repo actually uses is smaller and
 * says what it means, the same way `capture/webgazer.d.ts` declares only the
 * fact that the `webgazer` module exists.
 */
declare module "*.glb?url" {
  const url: string;
  export default url;
}
