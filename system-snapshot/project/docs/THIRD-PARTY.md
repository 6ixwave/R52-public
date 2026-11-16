# Third-party notices — R52 2.12

## Three.js r170

Three.js, OrbitControls and CSS3DRenderer are bundled locally. MIT license and original copyright notices are retained in r52/web/js/vendor/THREE-LICENSE.txt and source headers. Bundled-file checksums are in the archive-level SHA256SUMS-R52-2.12.txt. No CDN is required.

## React Bits Topography

Source: https://github.com/DavidHDev/react-bits/blob/main/src/content/Backgrounds/Topography/Topography.jsx

Copyright (c) 2026 David Haz. **MIT + Commons Clause License Condition v1.0**, not unrestricted MIT. The full notice and restrictions are in r52/web/js/vendor/REACT-BITS-LICENSE.txt.

R52 adapts the lattice/noise/relief routines in urban-layout.js to generate fixed building heights on street-aligned lots. The contour-stroke logic is adapted in city-materials.js for facades. R52's street layout, instanced architecture, pedestrian routes, residents, data binding and interface are application-specific implementation. The original React component and Background Studio tool are not redistributed as standalone components or services.

Since 2.12 the overview widget also runs the component itself: r52/web/js/vendor/topography.js is a vanilla-JS port of Topography.jsx (commit b0422bb475ac9ec1fe1680290970494ceff5d4eb, git blob aa0b5740a1246b79dbac366978989e2da8f19595) with the "Sonar" preset from src/demo/Backgrounds/presets/Topography.js. Shaders, constants, maths, the render loop and the interaction are unchanged; React hooks are replaced by createTopography(container, props), and an optional reducedMotion prop follows the R52 motion setting. The port keeps the copyright notice in its header, is covered by the same MIT + Commons Clause terms in REACT-BITS-LICENSE.txt and is distributed only as part of R52, not as a standalone component.

## Architectural reference, not bundled code

The public 3D-City project by Sampath0411 was examined as a Three.js city-design reference: https://github.com/Sampath0411/3D-City . Its engine and assets were not copied or bundled. R52 uses its existing Three.js r170 framework rather than a WebGPU-only city generator or an unverified external city-engine dependency.
