# Onshape Modeling Connector for Claude

A lightweight custom connector that lets Claude build and modify real 3D CAD geometry in Onshape using a small set of purpose-built FeatureScript tools.

The connector is designed around a simple principle: **compose useful mechanical parts from a small number of reliable primitives and operations**, rather than exposing the entire Onshape API.

## Features

The connector supports:

* Cylinders
* Arbitrary 2D polygon extrusions
* Tilted polygon extrusions
* Solids of revolution
* Boolean subtraction
* Circular patterns
* Uniform edge fillets
* Feature deletion
* Feature inspection
* FeatureScript evaluation
* Document management and activation

This makes it possible to create parts such as:

* Mounting plates
* Brackets
* Bosses
* Ribs and gussets
* Bolt-hole patterns
* Shafts and handles
* Knobs
* Rotational components
* Angled bosses
* Custom profiled bases

## How It Works

The connector uses a template Onshape document containing custom FeatureScript features.

When starting a new modeling project, the workflow is:

1. Copy the template document.
2. Locate its default Part Studio.
3. Resolve the FeatureScript namespace for that document.
4. Create the primary solid.
5. Add secondary geometry.
6. Create separate tool bodies for cuts.
7. Subtract the tool bodies from the target.
8. Apply patterns or fillets where required.
9. Inspect the resulting feature history when necessary.

Each copied Onshape document has its own FeatureScript namespace, so the namespace must always be resolved for the active document rather than hardcoded.

## Available Tools

### `copy_document`

Creates a new document from the template.

Returns:

* `newDocumentId`
* `newWorkspaceId`

The returned IDs should be passed to `get_default_part_studio`.

---

### `get_default_part_studio`

Finds the default Part Studio in a document and resolves the correct FeatureScript namespace.

This should be called immediately after `copy_document`.

It also activates the resulting Part Studio, allowing subsequent modeling operations to omit document and namespace parameters.

---

### `create_cylinder`

Creates a cylindrical solid.

```text
create_cylinder(
    radius_cm,
    depth_cm,
    plane="FRONT",
    x_cm=0,
    y_cm=0,
    z_cm=0,
    merge=True,
    name=...
)
```

Supported planes:

* `FRONT`
* `TOP`
* `RIGHT`

The first solid in an empty Part Studio must use:

```text
merge=False
```

Subsequent solids can use `merge=True` when they should fuse with the existing body.

---

### `create_sketch_extrude`

Creates an extruded solid from an arbitrary closed 2D polygon.

```text
create_sketch_extrude(
    points,
    depth_cm,
    plane="FRONT",
    x_cm=0,
    y_cm=0,
    z_cm=0,
    merge=True,
    draft_deg=0,
    draft_flip=False,
    name=...
)
```

For example, a rectangular block can be created with:

```text
points=[
    [0, 0],
    [3, 0],
    [3, 2],
    [0, 2]
]
```

The polygon is automatically closed, so the first point should not be repeated.

This tool can also create:

* Triangles
* Hexagons
* Octagons
* Chamfered profiles
* L-brackets
* Ribs
* Gussets
* Custom mechanical profiles

Draft angles must be supplied as positive values. Use `draft_flip=True` to reverse the taper direction.

---

### `create_tilted_sketch_extrude`

Creates an extruded polygon from a sketch plane tilted around its local X-axis.

```text
create_tilted_sketch_extrude(
    points,
    depth_cm,
    tilt_deg,
    plane="FRONT",
    merge=True,
    draft_deg=0,
    draft_flip=False,
    name=...
)
```

Useful for:

* Angled bosses
* Tilted mounting features
* Inclined pockets
* Non-perpendicular geometry

The polygon coordinates are expressed in the tilted plane's local coordinate system.

---

### `create_revolve`

Creates a solid of revolution.

```text
create_revolve(
    points,
    axis_x_cm,
    angle_deg,
    plane="FRONT",
    merge=True,
    name=...
)
```

The profile must remain entirely on one side of `axis_x_cm`.

A full rotational solid uses:

```text
angle_deg=360
```

This is useful for:

* Handles
* Knobs
* Shafts
* Floats
* Rotational housings
* Turned components

---

### `boolean_subtract`

Subtracts one solid from another.

```text
boolean_subtract(
    target_feature_id,
    tool_feature_id,
    name=...
)
```

Tool bodies should normally be created with:

```text
merge=False
```

For a through-hole, the cutting cylinder should extend beyond both sides of the target rather than ending exactly at its surfaces.

---

### `create_circular_pattern`

Creates evenly spaced copies of an existing feature.

```text
create_circular_pattern(
    source_feature_id,
    count,
    angle_step_deg,
    axis_x_cm=0,
    axis_y_cm=0
)
```

`count` is the **total number of instances**, including the original.

For four equally spaced instances:

```text
count=4
angle_step_deg=90
```

The rotation axis should always be explicitly positioned at the intended center.

---

### `fillet_all_edges`

Applies a uniform fillet to every edge of a solid.

```text
fillet_all_edges(
    target_feature_id,
    radius_cm
)
```

The operation is intentionally all-or-nothing: selective edge fillets are not supported.

Small radii are generally safer, especially on complex geometry.

---

### `delete_feature`

Permanently deletes a feature.

```text
delete_feature(feature_id)
```

Use `get_features` first if there is any uncertainty about which feature should be removed.

Deletion cannot be undone through the connector.

---

### `get_features`

Returns the feature history for a Part Studio.

```text
get_features(did, wid, eid)
```

This is useful for:

* Inspecting generated geometry
* Recovering feature IDs
* Debugging failed modeling operations
* Confirming the expected feature sequence

Feature IDs are nested inside the returned feature data, so callers should inspect the actual response structure rather than assuming a particular top-level field.

---

### `run_featurescript`

Evaluates a FeatureScript snippet without creating persistent geometry.

```text
run_featurescript(
    did,
    wid,
    eid,
    script
)
```

This is intended for queries and diagnostics, such as checking whether a reference resolves correctly.

It **does not create or modify geometry**.

---

### `list_documents`

Lists available Onshape documents.

```text
list_documents()
```

Useful for finding existing documents before activating one.

---

### `set_active_document`

Switches the connector to a specific document and Part Studio.

The active document's FeatureScript namespace is re-resolved when the document is activated.

This is preferable to reusing a namespace obtained from another document.

---

### `get_active_document`

Returns information about the currently active document and Part Studio.

Use this when the current modeling context is uncertain.

## Typical Modeling Workflow

A new project should follow this sequence:

```text
copy_document
        ↓
get_default_part_studio
        ↓
create primary solid
        ↓
create secondary solids
        ↓
create cutting bodies
        ↓
boolean_subtract
        ↓
patterns / fillets
        ↓
get_features
```

For example, a mounting bracket might be constructed as:

```text
1. Create a chamfered base profile.
2. Extrude the base.
3. Add cylindrical mounting bosses.
4. Add ribs or gussets.
5. Create hole cylinders as separate bodies.
6. Subtract the hole bodies.
7. Pattern repeated features where appropriate.
8. Apply a final fillet.
```

## Limitations

This connector intentionally exposes a limited modeling vocabulary.

It does not currently provide native tools for:

* Boxes
* Spheres
* Chamfers
* Selective edge fillets
* Arbitrary boolean union operations
* Native sketch editing
* Arbitrary reference-plane creation
* Full Onshape API functionality

Many shapes can nevertheless be constructed by combining polygon extrusions, cylinders, revolves, patterns, and boolean subtraction.

For example, a box is simply a four-vertex polygon extruded to the desired depth, while a chamfered box can be represented by an eight-vertex profile.

## Design Philosophy

The connector favors **predictable, composable operations** over exposing a large and fragile API surface.

Each modeling operation produces a feature that can be referenced by its `featureId`. Those features can then be combined, patterned, filleted, or subtracted to construct more complex geometry.

This approach also makes the generated CAD history easier to inspect and debug.

