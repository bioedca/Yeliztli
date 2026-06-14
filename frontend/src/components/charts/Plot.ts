/** Interop-safe re-export of react-plotly.js's `<Plot>` component.
 *
 * `react-plotly.js` is a Babel-compiled CommonJS module
 * (`exports.__esModule = true; exports.default = PlotComponent`). Under Vite 8's
 * "consistent CommonJS interop" — which mirrors Node's ESM semantics — a default
 * import in this `type: module` project resolves to the whole `module.exports`
 * object (`{ __esModule, default }`) rather than the component. That made every
 * Plotly chart crash at render with "Element type is invalid … got: object"
 * once the repo moved to Vite 8 (Vite 7 returned `.default`).
 *
 * Unwrap `.default` here — falling back to the import itself for bundlers and
 * test mocks that already hand back the component — so every chart gets a real
 * component regardless of interop behavior. All chart components import `<Plot>`
 * from this module rather than from `react-plotly.js` directly.
 */
import Plot from 'react-plotly.js'

const PlotComponent =
  (Plot as unknown as { default?: typeof Plot }).default ?? Plot

export default PlotComponent
