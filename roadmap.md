# Craft logistics and due timestamps

- [x] Show recording date/time beside due dates on both portals.
- [x] Add gram-based product weights with non-destructive automatic migration.
- [x] Plan sequential, capacity-safe craft trips, including prerequisite products.
- [x] Add per-step completed/remaining trip controls.
- [x] Test calculations, data preservation, and rendered user flow.

Verification: all 12 Python tests passed. The signed-in browser flow passed using an isolated in-memory database: capacity entry, exact ingredient loads, increment/undo, reload persistence, final partial trip, desktop/mobile layout, and production stock changes. Production server deployment and verification remain outside this workspace; no production data was accessed or changed.