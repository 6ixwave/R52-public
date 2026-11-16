import { cityEdge } from "./city-edge.js";
import * as THREE from "./vendor/three.module.js";
import { createUrbanPlan, advanceWalker, walkerPose } from "./urban-layout.js";
import { regionAt } from "./city-model.js";
import { urbanMaterials } from "./city-materials.js";
export function createUrbanWorld(model, own, { preview = false } = {}) {
  const plan = createUrbanPlan(model, { preview }),
    group = new THREE.Group(),
    materials = urbanMaterials();
  own(materials.towers);
  own(materials.ground);
  const box = own(new THREE.BoxGeometry(1, 1, 1)),
    dummy = new THREE.Object3D(),
    matrix = new THREE.Matrix4();
  const roots = model.root.children,
    extent = roots.length
      ? {
          minX:
            Math.min(
              ...roots.map((n) =>
                n.polygon
                  ? Math.min(...n.polygon.map((p) => p.x))
                  : n.position.x - n.radius,
              ),
            ) - 6,
          maxX:
            Math.max(
              ...roots.map((n) =>
                n.polygon
                  ? Math.max(...n.polygon.map((p) => p.x))
                  : n.position.x + n.radius,
              ),
            ) + 6,
          minZ:
            Math.min(
              ...roots.map((n) =>
                n.polygon
                  ? Math.min(...n.polygon.map((p) => p.z))
                  : n.position.z - n.radius,
              ),
            ) - 6,
          maxZ:
            Math.max(
              ...roots.map((n) =>
                n.polygon
                  ? Math.max(...n.polygon.map((p) => p.z))
                  : n.position.z + n.radius,
              ),
            ) + 6,
        }
      : {
          minX: -plan.half,
          maxX: plan.half,
          minZ: -plan.half,
          maxZ: plan.half,
        };
  const ground = new THREE.Mesh(
    own(
      new THREE.PlaneGeometry(
        extent.maxX - extent.minX,
        extent.maxZ - extent.minZ,
      ),
    ),
    materials.ground,
  );
  ground.rotation.x = -Math.PI / 2;
  ground.position.set(
    (extent.minX + extent.maxX) / 2,
    -0.06,
    (extent.minZ + extent.maxZ) / 2,
  );
  group.add(ground);
  group.add(cityEdge(model,own));
  const towers = new THREE.InstancedMesh(
      box,
      materials.towers,
      plan.lots.length,
    ),
    bases = new THREE.InstancedMesh(
      box,
      own(
        new THREE.MeshStandardMaterial({
          color: 0x28183f,
          roughness: 0.7,
          metalness: 0.3,
        }),
      ),
      plan.lots.length,
    ),
    upperLots = plan.lots.filter((p) => p.height > 9),
    upper = new THREE.InstancedMesh(box, materials.towers, upperLots.length),
    edgePoints = [];
  plan.lots.forEach((lot, i) => {
    dummy.position.set(lot.x, lot.height / 2, lot.z);
    dummy.scale.set(lot.width, lot.height, lot.depth);
    dummy.rotation.set(0, 0, 0);
    dummy.updateMatrix();
    towers.setMatrixAt(i, dummy.matrix);
    towers.setColorAt(
      i,
      new THREE.Color(regionAt(model, lot.x, lot.z)?.color || "#5b526e"),
    );
    dummy.position.y = 0.18;
    dummy.scale.set(lot.width + 0.6, 0.36, lot.depth + 0.6);
    dummy.updateMatrix();
    bases.setMatrixAt(i, dummy.matrix);
    const corners = [
      [-1, -1],
      [1, -1],
      [1, 1],
      [-1, 1],
    ];
    for (let c = 0; c < 4; c++) {
      const a = corners[c],
        b = corners[(c + 1) % 4];
      edgePoints.push(
        lot.x + (a[0] * lot.width) / 2,
        lot.height,
        lot.z + (a[1] * lot.depth) / 2,
        lot.x + (b[0] * lot.width) / 2,
        lot.height,
        lot.z + (b[1] * lot.depth) / 2,
      );
    }
  });
  upperLots.forEach((lot, i) => {
    const h = lot.height * 0.22;
    dummy.position.set(lot.x, lot.height + h / 2, lot.z);
    dummy.scale.set(lot.width * 0.68, h, lot.depth * 0.68);
    dummy.updateMatrix();
    upper.setMatrixAt(i, dummy.matrix);
    upper.setColorAt(
      i,
      new THREE.Color(regionAt(model, lot.x, lot.z)?.color || "#5b526e"),
    );
  });
  group.add(towers, bases, upper);
  const lotRegions = plan.lots.map((l) => regionAt(model, l.x, l.z)),
    upperRegions = upperLots.map((l) => regionAt(model, l.x, l.z)),
    colorCache = new Map();
  for (const node of model.nodes.filter((n) => n.kind === "area"))
    colorCache.set(node.key, new THREE.Color(node.color));
  let colorKey = "";
  const tint = new THREE.Color();
  function setDistrictDetail(states) {
    const weights = new Map(states.map((s) => [s.node.key, s.opacity]));
    const key = states
      .map((s) => s.node.key + ":" + Math.round(s.opacity * 16))
      .join("|");
    if (key === colorKey) return;
    colorKey = key;
    function recolor(mesh, regions) {
      regions.forEach((node, i) => {
        if (!node) {
          tint.set("#5b526e");
        } else {
          const chain = [];
          for (let n = node; n && n.kind === "area"; n = n.parent)
            chain.unshift(n);
          tint.copy(colorCache.get(chain[0].key));
          for (const n of chain.slice(1))
            tint.lerp(colorCache.get(n.key), weights.get(n.key) || 0);
        }
        mesh.setColorAt(i, tint);
      });
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }
    recolor(towers, lotRegions);
    recolor(upper, upperRegions);
  }
  setDistrictDetail(model.root.children.map((node) => ({ node, opacity: 1 })));
  const edgeGeo = own(new THREE.BufferGeometry());
  edgeGeo.setAttribute(
    "position",
    new THREE.Float32BufferAttribute(edgePoints, 3),
  );
  group.add(
    new THREE.LineSegments(
      edgeGeo,
      own(
        new THREE.LineBasicMaterial({
          color: 0xba82ff,
          transparent: true,
          opacity: 0.28,
        }),
      ),
    ),
  );
  const parkMaterial = own(
      new THREE.MeshStandardMaterial({ color: 0x211530, roughness: 1 }),
    ),
    treeGeometry = own(new THREE.ConeGeometry(0.8, 1.9, 7)),
    treeMaterial = own(
      new THREE.MeshStandardMaterial({ color: 0x56347a, roughness: 0.85 }),
    ),
    trees = new THREE.InstancedMesh(
      treeGeometry,
      treeMaterial,
      plan.parks.length * 4,
    );
  plan.parks.forEach((park, i) => {
    const pad = new THREE.Mesh(box, parkMaterial);
    pad.position.set(park.x, 0.03, park.z);
    pad.scale.set(9, 0.06, 9);
    group.add(pad);
    for (let j = 0; j < 4; j++) {
      dummy.position.set(
        park.x + (j % 2 ? 2.5 : -2.5),
        1.05,
        park.z + (j < 2 ? -2.5 : 2.5),
      );
      dummy.scale.setScalar(1);
      dummy.updateMatrix();
      trees.setMatrixAt(i * 4 + j, dummy.matrix);
    }
  });
  group.add(trees);
  // Sidewalk lamps are geometry, not hundreds of expensive point lights.
  const lamps = plan.lots.filter((_, i) => i % 8 === 0),
    posts = new THREE.InstancedMesh(
      box,
      own(new THREE.MeshBasicMaterial({ color: 0x71528f })),
      lamps.length,
    ),
    caps = new THREE.InstancedMesh(
      box,
      own(new THREE.MeshBasicMaterial({ color: 0xd2acff })),
      lamps.length,
    );
  lamps.forEach((lot, i) => {
    dummy.position.set(lot.x > 0 ? lot.x + 2.15 : lot.x - 2.15, 1.2, lot.z);
    dummy.scale.set(0.055, 2.4, 0.055);
    dummy.updateMatrix();
    posts.setMatrixAt(i, dummy.matrix);
    dummy.position.y = 2.43;
    dummy.scale.set(0.34, 0.07, 0.2);
    dummy.updateMatrix();
    caps.setMatrixAt(i, dummy.matrix);
  });
  group.add(posts, caps);
  const bodyMat = own(
      new THREE.MeshStandardMaterial({ color: 0x98cbe8, roughness: 0.8 }),
    ),
    headMat = own(
      new THREE.MeshStandardMaterial({ color: 0xffe0bc, roughness: 0.9 }),
    ),
    legMat = own(
      new THREE.MeshStandardMaterial({ color: 0x736fa0, roughness: 0.9 }),
    );
  const specs = [
    {
      geo: own(new THREE.CapsuleGeometry(0.13, 0.27, 3, 6)),
      mat: bodyMat,
      pos: [0, 0.58, 0],
    },
    {
      geo: own(new THREE.SphereGeometry(0.135, 7, 5)),
      mat: headMat,
      pos: [0, 0.91, 0],
    },
    {
      geo: own(new THREE.CylinderGeometry(0.047, 0.047, 0.34, 5)),
      mat: legMat,
      pos: [-0.075, 0.25, 0],
      leg: 1,
    },
    {
      geo: own(new THREE.CylinderGeometry(0.047, 0.047, 0.34, 5)),
      mat: legMat,
      pos: [0.075, 0.25, 0],
      leg: -1,
    },
    {
      geo: own(new THREE.CylinderGeometry(0.035, 0.035, 0.3, 5)),
      mat: bodyMat,
      pos: [-0.18, 0.57, 0],
      arm: -1,
    },
    {
      geo: own(new THREE.CylinderGeometry(0.035, 0.035, 0.3, 5)),
      mat: bodyMat,
      pos: [0.18, 0.57, 0],
      arm: 1,
    },
  ];
  for (const spec of specs) {
    spec.mesh = new THREE.InstancedMesh(
      spec.geo,
      spec.mat,
      plan.walkers.length,
    );
    spec.mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    spec.mesh.frustumCulled = false;
    group.add(spec.mesh);
  }
  const root = new THREE.Object3D(),
    part = new THREE.Object3D();
  let poses = [],
    lastPeopleTime = -Infinity,
    pendingPeopleDt = 0;
  const roofIndex = new Map();
  for (const lot of plan.lots) {
    const key = Math.floor(lot.x / 12) + "," + Math.floor(lot.z / 12);
    if (!roofIndex.has(key)) roofIndex.set(key, []);
    roofIndex.get(key).push(lot);
  }
  function roofHeight(x, z, radius = 0) {
    let h = 0;
    for (
      let ix = Math.floor((x - radius - 4) / 12);
      ix <= Math.floor((x + radius + 4) / 12);
      ix++
    )
      for (
        let iz = Math.floor((z - radius - 4) / 12);
        iz <= Math.floor((z + radius + 4) / 12);
        iz++
      )
        for (const lot of roofIndex.get(ix + "," + iz) || [])
          if (
            Math.abs(lot.x - x) <= lot.width / 2 + radius &&
            Math.abs(lot.z - z) <= lot.depth / 2 + radius
          )
            h = Math.max(h, lot.height * (lot.height > 9 ? 1.22 : 1));
    return h;
  }
  function update(time, dt, moving) {
    materials.uniforms.uTime.value = moving ? time : 0;
    pendingPeopleDt += dt;
    if (moving && time - lastPeopleTime < 0.048) return;
    dt = pendingPeopleDt;
    pendingPeopleDt = 0;
    lastPeopleTime = time;
    poses = [];
    for (const [i, w] of plan.walkers.entries()) {
      const pose = moving ? advanceWalker(w, dt) : walkerPose(w);
      poses.push(pose);
      root.position.set(pose.x, 0.07, pose.z);
      root.rotation.set(0, pose.yaw, 0);
      root.scale.setScalar(1.3);
      root.updateMatrix();
      const swing =
        moving && pose.walking
          ? Math.sin(time * 7 * w.speed + w.phase) * 0.42
          : 0;
      for (const spec of specs) {
        part.position.set(...spec.pos);
        part.rotation.set(swing * (spec.leg || spec.arm || 0), 0, 0);
        part.scale.setScalar(1);
        part.updateMatrix();
        matrix.multiplyMatrices(root.matrix, part.matrix);
        spec.mesh.setMatrixAt(i, matrix);
      }
    }
    for (const spec of specs) spec.mesh.instanceMatrix.needsUpdate = true;
  }
  group.traverse((object) => {
    if (object.isInstancedMesh) own(object);
  });
  update(0, 0, false);
  return {
    group,
    half: plan.half,
    update,
    setDistrictDetail,
    roofHeight,
    hover(point) {
      materials.uniforms.uHover.value.set(point?.x ?? 9999, point?.z ?? 9999);
    },
    pulse(point, time) {
      materials.uniforms.uPulse.value.set(point.x, point.z, time);
    },
    stats() {
      return {
        buildings: plan.lots.length,
        residents: plan.walkers.length,
        streetBlocks: plan.streetBlocks.length,
        blocks: plan.blocks,
        people: poses.slice(0, 4).map((p) => [p.x, p.z]),
      };
    },
  };
}
