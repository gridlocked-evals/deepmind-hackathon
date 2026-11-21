import type { StyleSpecification } from 'maplibre-gl';

export const MAP_STYLE_URL = 'https://tiles.openfreemap.org/styles/liberty';

/** A local scene remains usable even when the remote map is unavailable. */
export function fallbackStyle(): StyleSpecification {
  return {
    version: 8,
    name: 'Gotham Forge local scene',
    glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
    sources: {},
    layers: [{ id: 'ground', type: 'background', paint: { 'background-color': '#09121b' } }],
  };
}

/** Adapted from Pharos's style transformation; geographic data is unchanged. */
export function gothamStyle(style: StyleSpecification): StyleSpecification {
  for (const layer of style.layers) {
    const id = layer.id;
    if (layer.type === 'background') {
      layer.paint = { ...layer.paint, 'background-color': '#0b151e' };
    }
    if (layer.type === 'fill') {
      layer.paint = { ...layer.paint, 'fill-color': '#10202a', 'fill-outline-color': '#20333e' };
      delete layer.paint['fill-pattern'];
      if (/water/.test(id)) layer.paint = { ...layer.paint, 'fill-color': '#071923' };
      else if (/park|wood|grass|cemetery/.test(id)) {
        layer.paint = { ...layer.paint, 'fill-color': '#122725', 'fill-outline-color': '#1b3430' };
      } else if (/landuse|landcover/.test(id)) layer.paint = { ...layer.paint, 'fill-color': '#0f1c25' };
      else if (/building/.test(id)) {
        layer.paint = { ...layer.paint, 'fill-color': '#1a2b37', 'fill-outline-color': '#2b414f' };
      }
    }
    if (layer.type === 'line') {
      layer.paint = { ...layer.paint, 'line-color': '#263b47' };
      if (/waterway/.test(id)) layer.paint = { ...layer.paint, 'line-color': '#102d39' };
      else if (/boundary/.test(id)) layer.paint = { ...layer.paint, 'line-color': '#29404d', 'line-opacity': 0.5 };
      else if (/road|bridge|tunnel/.test(id) && !/rail/.test(id)) {
        layer.paint = {
          ...layer.paint,
          'line-color': /casing/.test(id) ? '#152530' : /motorway|trunk_primary/.test(id) ? '#3a4b52' : '#263b47',
        };
      } else if (/rail/.test(id)) layer.paint = { ...layer.paint, 'line-color': '#20323b' };
    }
    if (layer.type === 'fill-extrusion') {
      layer.paint = {
        ...layer.paint,
        'fill-extrusion-color': ['interpolate', ['linear'], ['coalesce', ['get', 'render_height'], 12],
          0, '#172b38', 35, '#203847', 100, '#2d4658', 250, '#3b5366'],
        'fill-extrusion-opacity': 0.97,
      };
    }
    if (layer.type === 'symbol') {
      if (/poi|highway-shield|road_shield|housenumber/.test(id)) {
        layer.layout = { ...layer.layout, visibility: 'none' };
      }
      if (layer.layout?.['text-field']) {
        layer.paint = {
          ...layer.paint,
          'text-color': /water/.test(id) ? '#42606c' : /place|country|state/.test(id) ? '#7b909b' : '#66808e',
          'text-halo-color': '#0a151d',
          'text-halo-width': 1.4,
        };
      }
    }
  }
  return style;
}
