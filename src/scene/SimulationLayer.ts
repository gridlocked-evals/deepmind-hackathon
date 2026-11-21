import { MercatorCoordinate, type CustomLayerInterface, type Map as MapLibreMap } from 'maplibre-gl';
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import type { ActorDefinition, ActorState, ScenarioBundle, SceneRuntime, Vec2 } from '../types';
import { ROAD_MAX, ROAD_MIN, STREET_BEARING, streetPoint } from './DemoScenarioProvider';

type SimulationMapLayer = CustomLayerInterface & { pickActor(point: { x: number; y: number }): string | null };
type Part = { geometry: THREE.BufferGeometry; material: THREE.Material; matrix: THREE.Matrix4; body?: boolean };

const ROAD_Z = 0.035;
const UP = new THREE.Vector3(0, 0, 1);

function material(color: string, roughness = 0.8, metalness = 0.1) {
  return new THREE.MeshStandardMaterial({ color, roughness, metalness });
}

function flatMaterial(color: string, opacity = 1) {
  return new THREE.MeshBasicMaterial({ color, transparent: opacity < 1, opacity, depthWrite: opacity === 1,
    side: THREE.DoubleSide });
}

function polygon(points: Vec2[], paint: THREE.Material, z = ROAD_Z) {
  const shape = new THREE.Shape();
  points.forEach(([x, y], index) => index ? shape.lineTo(x, y) : shape.moveTo(x, y));
  shape.closePath();
  const mesh = new THREE.Mesh(new THREE.ShapeGeometry(shape), paint);
  mesh.position.z = z;
  return mesh;
}

function streetBox(width: number, length: number, height: number, across: number, along: number, paint: THREE.Material, z = 0) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, length, height), paint);
  const [x, y] = streetPoint(across, along);
  mesh.position.set(x, y, z + height / 2);
  mesh.rotation.z = -STREET_BEARING;
  return mesh;
}

/** Profiles are written in the car's x/z plane; forward is +x, axles run along y. */
function profile(points: Vec2[], width: number) {
  const shape = new THREE.Shape(points.map(([x, z]) => new THREE.Vector2(x, z)));
  const geometry = new THREE.ExtrudeGeometry(shape, { depth: width, bevelEnabled: false, curveSegments: 1 });
  geometry.rotateX(Math.PI / 2);
  geometry.translate(0, width / 2, 0);
  return geometry;
}

function transform(x = 0, y = 0, z = 0, rx = 0, ry = 0, rz = 0) {
  return new THREE.Matrix4().compose(new THREE.Vector3(x, y, z),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(rx, ry, rz)), new THREE.Vector3(1, 1, 1));
}

function vehicleParts(body: THREE.Material, detailed: boolean): Part[] {
  const glass = material('#162c3a', 0.19, 0.4);
  const trim = material('#0b1118', 0.85, 0.08);
  const wheel = material('#070c12', 0.96, 0);
  const alloy = material('#8393a0', 0.3, 0.8);
  const headlight = new THREE.MeshStandardMaterial({ color: '#deefff', emissive: '#bddfff', emissiveIntensity: 1.2 });
  const taillight = new THREE.MeshStandardMaterial({ color: '#e94a49', emissive: '#c72d30', emissiveIntensity: 1.4 });
  const parts: Part[] = [
    { geometry: new RoundedBoxGeometry(4.7, 1.9, 0.65, 2, 0.15), material: body, matrix: transform(0, 0, 0.68), body: true },
    { geometry: new RoundedBoxGeometry(4.3, 1.82, 0.27, 1, 0.08), material: trim, matrix: transform(0, 0, 0.4) },
    { geometry: profile([[-1.65, 0.94], [-1.18, 1.6], [0.56, 1.6], [1.28, 0.94]], 1.64), material: glass, matrix: transform() },
    { geometry: new RoundedBoxGeometry(1.85, 1.67, 0.13, 1, 0.05), material: body, matrix: transform(-0.31, 0, 1.62), body: true },
    { geometry: new THREE.BoxGeometry(0.07, 1.1, 0.22), material: trim, matrix: transform(2.35, 0, 0.7) },
    { geometry: new THREE.BoxGeometry(0.09, 0.48, 0.15), material: headlight, matrix: transform(2.31, 0.64, 0.89) },
    { geometry: new THREE.BoxGeometry(0.09, 0.48, 0.15), material: headlight, matrix: transform(2.31, -0.64, 0.89) },
    { geometry: new THREE.BoxGeometry(0.08, 0.42, 0.16), material: taillight, matrix: transform(-2.34, 0.64, 0.88) },
    { geometry: new THREE.BoxGeometry(0.08, 0.42, 0.16), material: taillight, matrix: transform(-2.34, -0.64, 0.88) },
  ];
  const tireGeometry = new THREE.CylinderGeometry(0.37, 0.37, 0.23, detailed ? 20 : 12);
  const rimGeometry = new THREE.CylinderGeometry(0.23, 0.23, 0.25, detailed ? 12 : 8);
  for (const x of [-1.48, 1.48]) for (const y of [-0.91, 0.91]) {
    parts.push({ geometry: tireGeometry, material: wheel, matrix: transform(x, y, 0.39) });
    if (detailed) parts.push({ geometry: rimGeometry, material: alloy, matrix: transform(x, y, 0.39) });
  }
  if (detailed) {
    // The narrow pillars break up the glazing; door handles and mirrors read in follow mode.
    for (const y of [-0.835, 0.835]) {
      parts.push({ geometry: new THREE.BoxGeometry(0.11, 0.035, 0.59), material: body, matrix: transform(-0.36, y, 1.29) });
      for (const x of [-0.9, 0.44]) parts.push({ geometry: new THREE.BoxGeometry(0.2, 0.035, 0.045), material: alloy, matrix: transform(x, y * 1.13, 0.95) });
      parts.push({ geometry: new RoundedBoxGeometry(0.27, 0.2, 0.15, 1, 0.04), material: body, matrix: transform(0.85, y * 1.22, 1.13) });
      parts.push({ geometry: new THREE.BoxGeometry(1.76, 0.065, 0.06), material: trim, matrix: transform(-0.32, y * 0.75, 1.74) });
    }
    parts.push({ geometry: new THREE.BoxGeometry(0.07, 0.52, 0.12), material: flatMaterial('#e8edef'), matrix: transform(-2.4, 0, 0.64) });
    // A roof-mounted sensor establishes the focal vehicle silhouette without external assets.
    parts.push({ geometry: new THREE.CylinderGeometry(0.28, 0.32, 0.22, 24), material: body, matrix: transform(-0.2, 0, 1.84, Math.PI / 2) });
    parts.push({ geometry: new THREE.CylinderGeometry(0.29, 0.29, 0.075, 24), material: glass, matrix: transform(-0.2, 0, 1.92, Math.PI / 2) });
    parts.push({ geometry: new THREE.CylinderGeometry(0.24, 0.24, 0.035, 24), material: alloy, matrix: transform(-0.2, 0, 1.97, Math.PI / 2) });
  }
  return parts;
}

function makeRoute(points: Vec2[]) {
  const vertices: number[] = [], colors: number[] = [], indices: number[] = [];
  const forward = new THREE.Color('#1ed5ac');
  for (let i = 0; i < points.length; i++) {
    const previous = points[Math.max(0, i - 1)], next = points[Math.min(points.length - 1, i + 1)];
    const dx = next[0] - previous[0], dy = next[1] - previous[1], length = Math.hypot(dx, dy) || 1;
    const nx = -dy / length * 0.65, ny = dx / length * 0.65;
    vertices.push(points[i][0] + nx, points[i][1] + ny, 0.11, points[i][0] - nx, points[i][1] - ny, 0.11);
    colors.push(forward.r, forward.g, forward.b, forward.r, forward.g, forward.b);
    if (i < points.length - 1) { const j = i * 2; indices.push(j, j + 1, j + 2, j + 1, j + 3, j + 2); }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geometry.setIndex(indices);
  return new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.68,
    depthWrite: false, side: THREE.DoubleSide }));
}

export function createSimulationLayer(scenario: ScenarioBundle, runtime: SceneRuntime): SimulationMapLayer {
  let map: MapLibreMap | null = null;
  let renderer: THREE.WebGLRenderer | null = null;
  let scene: THREE.Scene | null = null;
  let camera: THREE.Camera | null = null;
  let egoGroup: THREE.Group | null = null;
  let route: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial> | null = null;
  let selection: THREE.Mesh | null = null;
  let shadows: THREE.InstancedMesh | null = null;
  let lastRouteTime = -1;
  let lastSignal = '';
  let lastDiagnostic = 0;
  let lastViewProjection: THREE.Matrix4 | null = null;
  const traffic = scenario.actors.filter(actor => actor.role !== 'ego').slice(0, 63);
  const ego = scenario.actors.find(actor => actor.role === 'ego');
  const batches: { mesh: THREE.InstancedMesh; part: Part }[] = [];
  const pickTargets: THREE.Object3D[] = [];
  const signalLights: { aspect: string; material: THREE.MeshStandardMaterial }[] = [];
  const actorMatrix = new THREE.Matrix4(), partMatrix = new THREE.Matrix4();
  const position = new THREE.Vector3(), scale = new THREE.Vector3(), quaternion = new THREE.Quaternion();
  const color = new THREE.Color();
  const origin = MercatorCoordinate.fromLngLat(scenario.origin, 0);
  const metres = origin.meterInMercatorCoordinateUnits();
  // MapLibre Mercator y points south, while scene y points north.
  const localToWorld = new THREE.Matrix4().makeTranslation(origin.x, origin.y, origin.z)
    .scale(new THREE.Vector3(metres, -metres, metres));

  function stateMatrix(state: ActorState, definition: ActorDefinition) {
    position.set(state.x, state.y, ROAD_Z);
    quaternion.setFromAxisAngle(UP, state.heading);
    scale.set(definition.length / 4.7, definition.width / 1.9, definition.height / 1.7);
    return actorMatrix.compose(position, quaternion, scale);
  }

  function createGround(target: THREE.Scene) {
    const roadPaint = material('#101b25', 0.99, 0);
    const sidewalkPaint = material('#334250', 1, 0);
    const curbPaint = material('#637580', 1, 0);
    const whitePaint = flatMaterial('#bac9ce', 0.71);
    const yellowPaint = flatMaterial('#bdab63', 0.68);
    for (const points of scenario.geometry.roads) target.add(polygon(points, roadPaint));
    for (const points of scenario.geometry.sidewalks) target.add(polygon(points, sidewalkPaint, 0.13));
    // Low curbs create useful parallax and ground the road plane at close range.
    target.add(streetBox(0.24, 400, 0.17, -15.12, 5, curbPaint));
    target.add(streetBox(0.24, 188, 0.17, 15.12, -100, curbPaint));
    target.add(streetBox(0.24, 198, 0.17, 15.12, 106, curbPaint));
    target.add(streetBox(3.45, 181, 0.18, 0, -104.5, sidewalkPaint));
    target.add(streetBox(3.45, 189, 0.18, 0, 110.5, sidewalkPaint));
    for (const across of [-14.7, 14.7]) target.add(streetBox(0.12, 400, 0.015, across, 5, whitePaint, 0.05));
    for (const across of [-1.9, 1.9]) {
      target.add(streetBox(0.12, 182, 0.015, across, -104, yellowPaint, 0.05));
      target.add(streetBox(0.12, 188, 0.015, across, 111, yellowPaint, 0.05));
    }
    // Instancing small road-marking rectangles keeps the close-up inexpensive.
    const markingMatrices: THREE.Matrix4[] = [];
    for (const across of [-10, -6, 6, 10]) for (let along = ROAD_MIN; along < ROAD_MAX; along += 8) {
      if (along > -17 && along < 19) continue;
      const [x, y] = streetPoint(across, along);
      markingMatrices.push(new THREE.Matrix4().compose(new THREE.Vector3(x, y, 0.07),
        new THREE.Quaternion().setFromAxisAngle(UP, -STREET_BEARING), new THREE.Vector3(0.13, 3.3, 0.014)));
    }
    for (const along of [-6.5, 8.5]) for (let across = -14; across < 15; across += 1.7) {
      if (Math.abs(across) < 2) continue;
      const [x, y] = streetPoint(across, along);
      markingMatrices.push(new THREE.Matrix4().compose(new THREE.Vector3(x, y, 0.074),
        new THREE.Quaternion().setFromAxisAngle(UP, -STREET_BEARING), new THREE.Vector3(0.77, 4.6, 0.016)));
    }
    const markings = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), whitePaint, markingMatrices.length);
    markingMatrices.forEach((matrix, i) => markings.setMatrixAt(i, matrix));
    markings.instanceMatrix.needsUpdate = true;
    target.add(markings);
    target.add(streetBox(12.5, 0.4, 0.025, 8.5, -15.5, whitePaint, 0.06));
    target.add(streetBox(12.5, 0.4, 0.025, -8.5, 17.5, whitePaint, 0.06));
    // Directional chevrons are individually authored road paint, not predicted trajectories.
    for (const across of [-12, -8, -4, 4, 8, 12]) for (const along of [-52, 57]) {
      const direction = across > 0 ? 1 : -1;
      const arrow: Vec2[] = [[-0.13, -1.8], [0.13, -1.8], [0.13, 0.9], [0.66, 0.35],
        [0.85, 0.55], [0, 1.6], [-0.85, 0.55], [-0.66, 0.35], [-0.13, 0.9]];
      target.add(polygon(arrow.map(([u, v]) => streetPoint(across + u, along + v * direction)), whitePaint, 0.08));
    }
    const polePaint = material('#5b6c77', 0.58, 0.45);
    const housingPaint = material('#131b22');
    for (const [u, v] of [[16.8, -15.5], [-16.8, 17.5]]) {
      const [x, y] = streetPoint(u, v);
      const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.11, 6.8, 8), polePaint);
      pole.rotation.x = Math.PI / 2; pole.position.set(x, y, 3.4); target.add(pole);
      const housing = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.44, 1.36), housingPaint);
      housing.position.set(x, y, 6.0); housing.rotation.z = -STREET_BEARING; target.add(housing);
      for (const [index, aspect] of ['red', 'amber', 'green'].entries()) {
        const paint = new THREE.MeshStandardMaterial({ color: '#253e3f', emissive: '#000000', roughness: 0.28 });
        const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.145, 10, 8), paint);
        bulb.position.set(x, y + (u > 0 ? -0.23 : 0.23), 6.42 - index * 0.4); target.add(bulb);
        signalLights.push({ aspect, material: paint });
      }
    }
    // Sparse riverside furniture lends scale without loading another asset collection.
    const trunkPaint = material('#293b3d');
    const canopyPaint = material('#233e41', 1);
    for (let along = -180; along < 200; along += 32) {
      const [x, y] = streetPoint(-19, along);
      const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.13, 0.2, 3.5, 5), trunkPaint);
      trunk.rotation.x = Math.PI / 2; trunk.position.set(x, y, 1.8);
      const canopy = new THREE.Mesh(new THREE.IcosahedronGeometry(2.4, 1), canopyPaint);
      canopy.position.set(x, y, 4.8); canopy.scale.z = 1.2;
      target.add(trunk, canopy);
    }
    // Static geometry of each material becomes one draw call; live signals keep
    // their separate materials, and the already-instanced paint stays instanced.
    const groups = new Map<THREE.Material, THREE.Mesh[]>();
    for (const object of target.children) {
      if (!(object instanceof THREE.Mesh) || object instanceof THREE.InstancedMesh || Array.isArray(object.material)) continue;
      const group = groups.get(object.material) ?? [];
      group.push(object); groups.set(object.material, group);
    }
    for (const [paint, meshes] of groups) {
      if (meshes.length < 2) continue;
      const transformed = meshes.map(mesh => {
        mesh.updateMatrixWorld(true);
        const geometry = mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry.clone();
        return geometry.applyMatrix4(mesh.matrixWorld);
      });
      const merged = mergeGeometries(transformed, false);
      transformed.forEach(geometry => geometry.dispose());
      if (!merged) continue;
      for (const mesh of meshes) { target.remove(mesh); mesh.geometry.dispose(); }
      target.add(new THREE.Mesh(merged, paint));
    }
  }

  const layer: SimulationMapLayer = {
    id: 'gotham-simulation', type: 'custom', renderingMode: '3d',
    onAdd(nextMap, gl) {
      map = nextMap;
      scene = new THREE.Scene(); camera = new THREE.Camera();
      scene.add(new THREE.HemisphereLight('#b9d6ee', '#27303a', 2.0));
      const key = new THREE.DirectionalLight('#e9f4ff', 3.2); key.position.set(-80, -40, 130); scene.add(key);
      const rim = new THREE.DirectionalLight('#6cadd2', 1.2); rim.position.set(90, 120, 65); scene.add(rim);
      if (scenario.geometryKind !== 'street-route') createGround(scene);
      route = makeRoute(scenario.geometry.route); scene.add(route);
      const trafficBody = material('#ffffff', 0.42, 0.24);
      for (const part of vehicleParts(trafficBody, false)) {
        const mesh = new THREE.InstancedMesh(part.geometry, part.material, traffic.length);
        mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
        mesh.frustumCulled = false;
        if (part.body) { mesh.userData.actorIds = traffic.map(actor => actor.id); pickTargets.push(mesh); }
        batches.push({ mesh, part }); scene.add(mesh);
      }
      if (ego) {
        egoGroup = new THREE.Group(); egoGroup.matrixAutoUpdate = false;
        for (const part of vehicleParts(material('#e3edf1', 0.28, 0.28), true)) {
          const mesh = new THREE.Mesh(part.geometry, part.material); mesh.matrix.copy(part.matrix); mesh.matrixAutoUpdate = false;
          mesh.userData.actorId = ego.id;
          if (part.body) pickTargets.push(mesh);
          egoGroup.add(mesh);
        }
        scene.add(egoGroup);
      }
      shadows = new THREE.InstancedMesh(new THREE.CircleGeometry(1, 20), flatMaterial('#02090f', 0.45), Math.min(64, scenario.actors.length));
      shadows.instanceMatrix.setUsage(THREE.DynamicDrawUsage); shadows.frustumCulled = false; scene.add(shadows);
      selection = new THREE.Mesh(new THREE.RingGeometry(2.7, 2.77, 64), flatMaterial('#78cde2', 0.5));
      selection.position.z = 0.095; scene.add(selection);
      renderer = new THREE.WebGLRenderer({ canvas: nextMap.getCanvas(), context: gl, antialias: true });
      renderer.autoClear = false;
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 0.9;
    },
    render(_gl, input) {
      if (!map || !renderer || !scene || !camera) return;
      // At the city scale the 4m vehicles are subpixel. Defer their draw/shader work
      // until the user approaches the local scene, leaving city navigation light.
      if (map.getZoom() < 16.5) { lastViewProjection = null; return; }
      const frame = runtime.frameRef.current;
      const options = runtime.optionsRef.current;
      const byId = new Map(frame.actors.map(actor => [actor.id, actor]));
      const selected = byId.get(options.selectedActorId) ?? (ego ? byId.get(ego.id) : undefined);
      for (const { mesh, part } of batches) {
        mesh.visible = options.actorsVisible;
        traffic.forEach((definition, index) => {
          const state = byId.get(definition.id);
          if (state) partMatrix.copy(stateMatrix(state, definition)).multiply(part.matrix);
          else partMatrix.makeScale(0, 0, 0);
          mesh.setMatrixAt(index, partMatrix);
          if (part.body) mesh.setColorAt(index, color.set(state?.disabled ? '#c64b32' : definition.id === options.selectedActorId ? '#78c6d8' : definition.color));
        });
        mesh.instanceMatrix.needsUpdate = true;
        if (part.body && mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      }
      if (ego && egoGroup) {
        const state = byId.get(ego.id); egoGroup.visible = options.actorsVisible && !!state;
        if (state) { egoGroup.matrix.copy(stateMatrix(state, ego)); egoGroup.matrixWorldNeedsUpdate = true; }
      }
      if (shadows) {
        shadows.visible = options.actorsVisible;
        scenario.actors.slice(0, 64).forEach((definition, index) => {
          const state = byId.get(definition.id);
          if (state) {
            position.set(state.x, state.y, 0.052); quaternion.setFromAxisAngle(UP, state.heading);
            partMatrix.compose(position, quaternion, scale.set(definition.length * 0.57, definition.width * 0.65, 1));
          } else partMatrix.makeScale(0, 0, 0);
          shadows!.setMatrixAt(index, partMatrix);
        });
        shadows.instanceMatrix.needsUpdate = true;
      }
      if (selection) {
        selection.visible = options.actorsVisible && !!selected;
        if (selected) selection.position.set(selected.x, selected.y, 0.095);
      }
      if (route) {
        route.visible = options.routesVisible;
        const egoState = ego ? byId.get(ego.id) : undefined;
        if (egoState && Math.abs(frame.time - lastRouteTime) > 0.08) {
          const colors = route.geometry.getAttribute('color') as THREE.BufferAttribute;
          // Progress follows the actual polyline, including east/west streets and turns.
          let nearestDistance = Infinity, nearestIndex = 0;
          scenario.geometry.route.forEach(([x, y], index) => {
            const distance = Math.hypot(x - egoState.x, y - egoState.y);
            if (distance < nearestDistance) { nearestDistance = distance; nearestIndex = index; }
          });
          const past = new THREE.Color('#1b71cf'), future = new THREE.Color('#16d2a5');
          scenario.geometry.route.forEach((_, index) => {
            const tint = index < nearestIndex ? past : future;
            colors.setXYZ(index * 2, tint.r, tint.g, tint.b); colors.setXYZ(index * 2 + 1, tint.r, tint.g, tint.b);
          });
          colors.needsUpdate = true; lastRouteTime = frame.time;
        }
      }
      if (lastSignal !== frame.signal) {
        for (const light of signalLights) {
          const active = light.aspect === frame.signal;
          light.material.color.set(active ? { green: '#26e1ad', amber: '#ffc45b', red: '#ff635c' }[light.aspect]! : '#243538');
          light.material.emissive.copy(light.material.color); light.material.emissiveIntensity = active ? 2.4 : 0;
        }
        lastSignal = frame.signal;
      }
      // MapLibre 6 owns camera and canvas sizing. We only share its GL context and matrix.
      // mainMatrix accepts normalized Mercator coordinates. modelViewProjectionMatrix
      // instead accepts internal world pixels and would place this metre-based scene offscreen.
      camera.projectionMatrix.fromArray(input.defaultProjectionData.mainMatrix).multiply(localToWorld);
      camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
      lastViewProjection = camera.projectionMatrix.clone();
      scene.updateMatrixWorld(true);
      const framebuffer = _gl.getParameter(_gl.FRAMEBUFFER_BINDING) as WebGLFramebuffer | null;
      const depthRange = _gl.getParameter(_gl.DEPTH_RANGE) as Float32Array;
      renderer.resetState();
      // A map render pass may own an offscreen framebuffer. Three's resetState
      // binds the default framebuffer, so restore the map's destination explicitly.
      _gl.bindFramebuffer(_gl.FRAMEBUFFER, framebuffer);
      renderer.setViewport(0, 0, map.getCanvas().width, map.getCanvas().height);
      renderer.render(scene, camera);
      // Record the frame actually drawn, including the rendered ego transform in
      // local metres. Keep these current when a drive pauses between decisions.
      const canvas = map.getCanvas();
      canvas.dataset.sceneScenarioId = scenario.id;
      canvas.dataset.sceneFrameTime = String(frame.time);
      canvas.dataset.sceneEgoX = egoGroup?.visible ? String(egoGroup.matrixWorld.elements[12]) : '';
      canvas.dataset.sceneEgoY = egoGroup?.visible ? String(egoGroup.matrixWorld.elements[13]) : '';
      if (performance.now() - lastDiagnostic > 1000) {
        canvas.dataset.sceneDrawCalls = String(renderer.info.render.calls);
        canvas.dataset.sceneTriangles = String(renderer.info.render.triangles);
        canvas.dataset.sceneFramebuffer = framebuffer ? 'map-owned' : 'default';
        canvas.dataset.sceneDepthRange = Array.from(depthRange).join(',');
        canvas.dataset.sceneGlError = String(_gl.getError());
        if (selected) {
          const ndc = new THREE.Vector3(selected.x, selected.y, 1).applyMatrix4(camera.projectionMatrix);
          canvas.dataset.sceneActorNdc = `${ndc.x.toFixed(3)},${ndc.y.toFixed(3)},${ndc.z.toFixed(6)}`;
          canvas.dataset.sceneActorClipW = String(new THREE.Vector4(selected.x, selected.y, 1, 1).applyMatrix4(camera.projectionMatrix).w);
        }
        lastDiagnostic = performance.now();
      }
      renderer.resetState();
    },
    pickActor(point) {
      if (!map || !lastViewProjection || !runtime.optionsRef.current.actorsVisible) return null;
      const canvas = map.getCanvas();
      const x = point.x / canvas.clientWidth * 2 - 1, y = 1 - point.y / canvas.clientHeight * 2;
      const inverse = lastViewProjection.clone().invert();
      const near = new THREE.Vector3(x, y, -1).applyMatrix4(inverse);
      const far = new THREE.Vector3(x, y, 1).applyMatrix4(inverse);
      const ray = new THREE.Raycaster(near, far.sub(near).normalize());
      const hits = ray.intersectObjects(pickTargets, false);
      const hit = hits[0];
      if (!hit) return null;
      return hit.instanceId !== undefined ? hit.object.userData.actorIds?.[hit.instanceId] ?? null : hit.object.userData.actorId ?? null;
    },
    onRemove() {
      const geometries = new Set<THREE.BufferGeometry>(), materials = new Set<THREE.Material>();
      scene?.traverse(object => {
        if (object instanceof THREE.Mesh) {
          geometries.add(object.geometry);
          for (const paint of Array.isArray(object.material) ? object.material : [object.material]) materials.add(paint);
          if (object instanceof THREE.InstancedMesh) object.dispose();
        }
      });
      geometries.forEach(geometry => geometry.dispose()); materials.forEach(paint => paint.dispose());
      renderer?.dispose();
      batches.length = 0; pickTargets.length = 0; signalLights.length = 0;
      map = null; renderer = null; scene = null; camera = null; route = null; selection = null;
      shadows = null; egoGroup = null; lastViewProjection = null;
    },
  };
  return layer;
}
