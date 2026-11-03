# Retained Orphic application design

`web/theme.css` reproduces the supplied Orphic Design Kit stylesheet. Its
ODSP Terminal variant is selected at the scoped
`.orphic[data-orphic-theme="odsp-terminal"]` root. The retained kit also contains
Corporate and Dark variables; this application does not mix those variants.
`web/styles.css` adapts the existing operator-console layout using the retained
tokens and `os-` primitives. It contains no external font or image dependency.

The supplied implementation contract requires a dominant inspection/work area,
supporting evidence, square panels and thin rules. Explanations use sans-serif;
identifiers, parameters, provenance and readings use monospace. Amber identifies
an action or emphasis and never certifies success or authority. Existing console
headings, canonical operation names and DOM identifiers remain available.

State labels must be truthful and visible without colour: loading, denied,
failed, partial, empty, stale, disconnected and returned data remain distinct.
Readiness is not execution authority. Unknown outcomes preserve receipts and
holds; neither reconnect nor a style change clears them.

Visual acceptance requires actual browser interactions and screenshots at
320, 375, 768 and 1440 pixels, 200% zoom, long identifiers, keyboard focus and
reduced motion. Normal text pairs require at least 4.5:1 contrast and necessary
graphical boundaries at least 3:1. Token values alone are not proof that every
rendered state meets those criteria. Report the exact tested source and
remaining gaps in the release evidence.

The retained starter's previous checks were static. Neither the design kit nor
this document establishes production integration, live MCP operation, native
solver qualification, deployment or physical certification.
