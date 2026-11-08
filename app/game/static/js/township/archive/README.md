# Archived township renderers

Nothing in this folder is loaded by any page. It is kept because it is work
someone may want to pick back up, not because anything depends on it.

## `village3.js`

An alternative renderer built during the v2 work: **v1's organic, non-grid
street layout drawn with v2's volumetric isometric technique**, with houses
that have car parking, an emergency fleet (ambulance, fire truck, police car)
and farm vehicles.

It was never adopted — the decision was to keep the v2 layout
(`../village2.js`), which `township-v2.html` loads. `village3.js` is a
complete, self-contained module with the same public surface as
`village2.js` (`mount`, `fit`, `zoomBy`, `setInteriors`, `setAgents`, …), so
swapping it in is a one-line change to the `<script src>` in
`static/township-v2.html` — but it has **not** been kept in step with
`village2.js` since, and is missing everything added after the split:
interiors furniture kits, verge planting, street lighting, critters, the
reclining sleep pose and the zoom-aware figure scale.

Treat it as a design reference, not as a drop-in.
