# Evidence-bound portrait cache

Signal Board portraits are optional presentation evidence. Report rendering is
offline and never downloads a face.

An automated portrait may be declared directly on a stored decision-maker row,
or in `index.json` with this shape:

```json
{
  "schema_version": 1,
  "portraits": [
    {
      "name": "Exact published name",
      "organization": "Exact organization",
      "source_system": "official-biography",
      "source_record_id": "https://agency.gov/official-biography",
      "portrait_source_url": "https://agency.gov/official-portrait",
      "asset": "data/reference/portraits/example.png",
      "sha256": "64 lowercase hexadecimal characters"
    }
  ]
}
```

The first four fields must exactly match the rendered person record. The image
must be PNG, JPEG, or WebP, remain below 2 MiB, and match the recorded SHA256.
Conflicting exact matches, unsafe paths, missing source URLs, bad hashes, and
unreadable images all fail to the editable initials placeholder.

Operator drag/drop portraits live in the client presentation manifest instead.
Their identity is still the full person/source claim, so a saved image cannot
bleed onto a namesake's card.
