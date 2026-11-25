# Lithuanian buildings and ordinary road signs

## Terrain streaming fix (`lithuania.dev.2`)

The first dev build incorrectly called `Dem._partial()` even after a successful Lithuanian terrain
read. That helper also reports a source failure to the world-plan cache. Valid terrain was therefore
treated as a temporary fallback: ancestors were repeatedly loaded instead of reused, and dependent
road/bridge plans could not be retained as complete. Trees queued behind those road jobs also lagged.
The fix separates skipping the decoded DEM mirror from reporting a failed source. Only a real
terrain fetch/decode failure now faults the dependent plans. A regression test exercises the real
DEM load, fault propagation and sibling-ancestor reuse; outage fallback remains marked incomplete.
The in-game disappearance was reported in Kaunas when enabling the terrain option and reversed
when disabling it. The corrected executable still needs the same in-game comparison.

Implemented in the Lithuania fork, November 2026. These changes reuse the existing procedural
building meshes, stock building materials and the project's own Vienna sign atlas. No national
model download, extra service, account or native rebuild is needed. Game appearance remains
unverified; Python and native mesh tests cover the generation path.

## Buildings

`buildings.region_style` now selects a Lithuania palette before the broad northern-European and
Nordic defaults. A bounding box only limits lookups; the existing streamed country-border index
must return `LT`. Neighbouring countries retain their previous style. If the border data is
unavailable, generation keeps the old regional fallback. The per-position lookup is bounded and
cached by border-index identity so a newly available index replaces an absent one.

The fallback mixes plaster, brick and timber houses, quieter plaster colours, and dark, metal
or clay-tile roofs. Default house roof pitch is 35 degrees. It removes the previous default
of Scandinavian red barns in the northern part of Lithuania, and the generic northern-European
stone share elsewhere. Existing mapped roof shape, material, height and floors keep precedence.
The style weights are artistic starting points, **not measured national statistics**. They do
not distinguish Kaunas interwar modernism, Vilnius Old Town or individual apartment estates.

References for the range of building types, rather than for the numerical weights:

- [Go Vilnius: Žvėrynas](https://www.govilnius.lt/visit-vilnius/places/zverynas), traditional wooden housing.
- [Kaunas 2022: C. Pacevičius villa](https://kaunas2022.eu/modernizmasateiciai/en/c-paceviciaus-vila/index.html),
  an example of rendered interwar modernist architecture.

## Road signs

Enable **Road signs** in Settings > Experimental and restart the application (or set
`BNE_ROADSIGNS=on`). Lithuania now has a `vienna_w` profile, explicitly restricted to 12 reviewed
roles: give-way, stop, no-entry, roundabout warning and mandatory roundabout, signal warning,
pedestrian crossing, one-way, parking, bus stop, residential zone and its end.

The existing renderer places these using road topology and available mapped infrastructure.
Placement is inferred, not a survey of actual poles. Mounts, offsets and stacking remain
representative renderer settings, not a complete implementation of Lithuanian installation rules.
Shared atlas pictograms approximate national artwork. There are no speed-limit or motorway
artwork changes in this patch; Lithuanian motorway boards need green artwork and are excluded
from this ordinary-sign profile.

The OSM code parser now recognises Lithuanian numbers 111, 112, 201–206, 301, 410, 503, 528,
534, 548, 552 and 553, and recognises 800-series supplementary plates as metadata. Parsing a code
does not add a new placement rule: priority-road and oncoming-priority roles remain parser support
only. Supplementary-plate text is not rendered. Unknown codes remain omitted. Left-facing
crossing 533 and dead-end codes are deliberately omitted pending matching artwork/current-number
review. All new mapped codes have catalogue roles and are covered by parser tests.

Number references:

- [Lithuanian Road Traffic Rules, annex 1](https://e-seimas.lrs.lt/rs/actualedition/TAIS.203613/hUjhFmAcWQ/).
- [Road-sign installation rules](https://e-seimas.lrs.lt/rs/actualedition/TAIS.418378/YoUZyzkrnM/),
  including roundabouts, one-way streets, crossings, parking and stops.

Direction boards are a separate system. Its bundled destination snapshot contains no Lithuanian
places, so merely adding a Lithuanian colour profile would produce no useful destination signs.
A future patch needs a small licensed destination gazetteer and the Lithuanian board styles,
including green motorway boards and correct diacritics. Unique landmark models likewise need
usable geometry and a verified licence; this patch adds no claimed replicas.

## Validation

Tests cover selection in Kaunas, Vilnius and Šiauliai with a supplied border index; neighbouring
country and unavailable-border fallbacks; mapped attribute precedence; deterministic building
choices; Lithuanian OSM code parsing; supplementary-plate handling; the reviewed-role filter;
and native mesh generation with atlas cells restricted to the enabled Lithuanian roles.
The existing source-code cache fingerprint includes `buildings.py`, so generated scenery from
the previous fork commit is invalidated when this version starts. No manual cache deletion is needed.
