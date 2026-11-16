import {
  MIN_DISTANCE,
  MIN_ALTITUDE,
  MAX_POLAR,
  zoomStep,
  wheelInput,
  flightDuration,
  cityBounds,
  boundedTarget,
  flightPose,
  flightEase,
  dampDistance,
} from "./city-navigation.js";
import * as THREE from "./vendor/three.module.js";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { h, money } from "./dom.js";
import { photoGallery, activateMedia } from "./media.js";
import { motionOn } from "./motion.js";
import { createUrbanWorld } from "./urban-world.js";
import { createCityPanels } from "./city-panels.js";
import { createDistrictLabels } from "./city-district-labels.js";
import { fitCamera, projectedFit, CITY_ELEVATION, FRAME_ELEVATION } from "./city-camera-fit.js";

// One reusable context across overview/map navigation prevents repeated driver allocations.
let parkedRenderer = null;

// Buildings stay in WebGL; readable HTML annotations keep a stable screen size.
// Their leader lines project the real product roof through the same camera.
export function createCity(
  host,
  { onSelect, onOpen, onAsk, onReady, onError, preview = false } = {},
) {
  let alive = true,
    visible = true,
    dirty = true,
    frame = 0,
    last = 0,
    topology = null,
    introPending = !preview,
    selection = null,
    tween = null,
    board = null,
    selectedGallery = null,
    down = null,
    hover = null,
    navigationBounds = null,
    lastHoverHit = 0,
    contextLost = false,
    urban = null,
    urbanLast = 0,
    immersive = false,
    // Automatic framing refits on resize until the user takes the camera.
    autoFrame = false,
    pendingFrame = null,
    framing = null,
    zoomGoal = null,
    zoomAt = 0,
    fastUntil = 0,
    freeView = null,
    freeViewKey = "",
    freeViewAt = 0;
  const colliders = [],
    disposables = new Set();
  const own = (value) => (disposables.add(value), value);
  const scene = new THREE.Scene(),
    camera = new THREE.PerspectiveCamera(42, 1, 0.05, 1600);
  scene.fog = new THREE.FogExp2(0x0a0714, 0.0038);
  let renderer;
  try {
    if (parkedRenderer && !parkedRenderer.getContext().isContextLost()) {
      renderer = parkedRenderer;
      parkedRenderer = null;
      renderer.resetState();
    } else {
      parkedRenderer?.dispose();
      parkedRenderer = null;
      renderer = new THREE.WebGLRenderer({
        alpha: true,
        antialias: true,
        powerPreference: "low-power",
        preserveDrawingBuffer: true,
      });
    }
  } catch (error) {
    throw new Error(
      "WebGL 2 недоступен. Откройте обычный каталог или включите аппаратное ускорение браузера.",
    );
  }
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.5));
  renderer.setClearColor(0x050b15, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.domElement.className = "city-webgl";
  renderer.domElement.tabIndex = preview ? -1 : 0;
  renderer.domElement.setAttribute(
    "aria-label",
    "3D-город. Перетаскивание — перемещение; правая кнопка — вращение; колесо — масштаб. Для выбора с клавиатуры используйте список областей.",
  );
  host.append(renderer.domElement);
  let panelHover = null;
  // A hovered callout lights its building, like hovering the roof itself.
  const panels = preview
    ? null
    : createCityPanels(host, onSelect, (entity) => {
        if (panelHover !== entity) {
          panelHover = entity;
          dirty = true;
        }
      });
  const districts = preview
    ? null
    : createDistrictLabels(host, onSelect, { scene });
  let spatialStates = [];
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.085;
  controls.minDistance = MIN_DISTANCE;
  controls.maxDistance = 900;
  controls.minPolarAngle = 0.3;
  controls.maxPolarAngle = MAX_POLAR;
  controls.screenSpacePanning = false;
  controls.mouseButtons.LEFT = THREE.MOUSE.PAN;
  controls.mouseButtons.RIGHT = THREE.MOUSE.ROTATE;
  controls.touches.ONE = THREE.TOUCH.PAN;
  controls.touches.TWO = THREE.TOUCH.DOLLY_ROTATE;
  controls.zoomSpeed = 1.2;
  controls.panSpeed = 0.6;
  controls.enabled = !preview;
  if (!preview) controls.listenToKeyEvents(renderer.domElement);
  camera.position.set(32, 58, 46);
  controls.target.set(0, 0, 0);
  controls.update();
  scene.add(new THREE.AmbientLight(0xe5d4ff, 1.6));
  const sun = new THREE.DirectionalLight(0xf0dfff, 2.1);
  sun.position.set(-20, 50, 30);
  scene.add(sun);
  const fill = new THREE.PointLight(0x822dd8, 80, 150);
  fill.position.set(0, 14, -10);
  scene.add(fill);
  const ray = new THREE.Raycaster(),
    pointer = new THREE.Vector2();
  function line(points, color, opacity = 1) {
    const geo = own(
        new THREE.BufferGeometry().setFromPoints(
          points.map((p) => new THREE.Vector3(...p)),
        ),
      ),
      mat = own(
        new THREE.LineBasicMaterial({
          color,
          transparent: true,
          opacity,
          depthTest: false,
          fog: false,
        }),
      );
    return new THREE.Line(geo, mat);
  }
  function floor(node) {
    const group = new THREE.Group();
    group.position.set(
      node.position.x,
      0.025 + node.depth * 0.003,
      node.position.z,
    );
    const r = node.radius,
      polygon = node.polygon?.map((p) => ({
        x: p.x - node.position.x,
        z: p.z - node.position.z,
      })) || [
        { x: -r, z: -r },
        { x: r, z: -r },
        { x: r, z: r },
        { x: -r, z: r },
      ],
      material = own(
        new THREE.MeshBasicMaterial({
          color: node.color,
          transparent: true,
          opacity: 0,
          depthTest: false,
          fog: false,
          depthWrite: false,
        }),
      ),
      mesh = new THREE.Mesh(
        own(
          new THREE.ShapeGeometry(
            new THREE.Shape(polygon.map((p) => new THREE.Vector2(p.x, -p.z))),
          ),
        ),
        material,
      );
    mesh.rotation.x = -Math.PI / 2;
    mesh.userData.entity = node;
    colliders.push(mesh);
    group.add(mesh);
    const outline = line(
      [...polygon, polygon[0]].map((p) => [p.x, 0.02, p.z]),
      node.color,
      node.depth ? 0.17 : 0.4,
    );
    group.add(outline);
    mesh.renderOrder = 2;
    outline.renderOrder = 3;
    node.visual = {
      group,
      mesh,
      material,
      outline,
      targetOpacity: 0,
      targetOutline: 0,
    };
    return group;
  }
  function building(entity) {
    const width = Math.min(3.4, entity.step * 0.43),
      height =
        1.5 +
        Math.min(1.8, entity.step * 0.16) +
        (Number(entity.product.id) % 4) * 0.22,
      group = new THREE.Group();
    group.position.set(entity.position.x, 0, entity.position.z);
    entity.roofHeight = height;
    const geo = own(new THREE.BoxGeometry(width, height, width * 0.86)),
      mat = own(
        new THREE.MeshStandardMaterial({
          color: entity.color,
          emissive: entity.color,
          emissiveIntensity: 0.22,
          metalness: 0.4,
          roughness: 0.44,
        }),
      ),
      mesh = new THREE.Mesh(geo, mat);
    mesh.position.y = height / 2;
    mesh.userData.entity = entity;
    group.add(mesh);
    colliders.push(mesh);
    const edges = new THREE.LineSegments(
      own(new THREE.EdgesGeometry(geo)),
      own(
        new THREE.LineBasicMaterial({
          color: 0xc796ff,
          transparent: true,
          opacity: 0.65,
        }),
      ),
    );
    edges.position.copy(mesh.position);
    group.add(edges);
    const ring = new THREE.Mesh(
      own(new THREE.PlaneGeometry(width + 1.1, width + 1.1)),
      own(
        new THREE.MeshBasicMaterial({
          color: 0xb377fa,
          transparent: true,
          opacity: 0.14,
          depthWrite: false,
        }),
      ),
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.03;
    group.add(ring);
    const roof = new THREE.Mesh(
      own(new THREE.BoxGeometry(width * 0.7, 0.12, width * 0.58)),
      own(new THREE.MeshBasicMaterial({ color: 0xc392ff })),
    );
    roof.position.y = height + 0.06;
    group.add(roof);
    entity.visual = { group, mesh, mat, ring };
    return group;
  }
  function clearWorld() {
    removeGallery();
    panels?.clear();
    districts?.clear();
    if (board) scene.remove(board);
    for (const value of disposables) value.dispose?.();
    disposables.clear();
    colliders.length = 0;
    board = null;
    urban = null;
  }
  function setData(model) {
    clearWorld();
    topology = model;
    navigationBounds = cityBounds(model);
    controls.maxDistance = Math.max(
      360,
      model.root.radius * 5.5 * (camera.aspect < 0.8 ? 1.6 : 1),
    );
    panels?.setData(model);
    selection = null;
    board = new THREE.Group();
    scene.add(board);
    urban = createUrbanWorld(model, own, { preview });
    board.add(urban.group);
    for (const node of model.nodes.filter((n) => n.kind === "area"))
      board.add(floor(node));
    for (const entity of model.products) board.add(building(entity));
    setSelection(model.root);
    onReady?.({
      webgl: true,
      revision: THREE.REVISION,
      objects: model.products.length,
      records: model.total,
      ...urban.stats(),
    });
    dirty = true;
  }
  function removeGallery() {
    if (!selectedGallery) return;
    selectedGallery.el.querySelectorAll("video").forEach((v) => {
      v.pause();
      v.removeAttribute("src");
      v.load();
    });
    panels?.removeGallery();
    selectedGallery = null;
  }

  function addGallery(entity) {
    const p = entity.product;
    const header = h(
      "header",
      { class: "city-expanded-header" },
      h("span", { text: p.source }),
      h("button", {
        type: "button",
        "aria-label": "Закрыть объект",
        text: "×",
        onClick: () => onSelect?.(entity.parent),
      }),
    );
    const body = h(
      "div",
      { class: "city-expanded-body" },
      photoGallery(p),
      h(
        "h2",
        null,
        h("a", {
          href: `#/products/${p.id}`,
          class: "city-product-link",
          text: p.title,
        }),
      ),
      h("b", {
        class: "city-expanded-price",
        text:
          p.lowest_minor == null
            ? "Цена неизвестна"
            : money(p.lowest_minor, p.currency || "RUB"),
      }),
      h(
        "div",
        { class: "city-expanded-meta" },
        h("span", { text: "Площадка: " + p.source }),
        h("span", { text: "Наблюдений: " + (p.observation_count || 0) }),
        h("small", {
          text:
            "Фото: " +
            (p.photo_count || 0) +
            " · видео: " +
            (p.video_count || 0),
        }),
      ),
    );
    const actions = h(
      "div",
      { class: "city-expanded-actions" },
      h("button", {
        type: "button",
        text: "Спросить об этом",
        onClick: () => onAsk?.(entity),
      }),
    );
    const el = h(
      "article",
      { class: `city-expanded-card market-${p.source}` },
      header,
      body,
      actions,
    );
    el.style.pointerEvents = "auto";
    el.style.borderColor = entity.color + "88";
    for (const event of ["pointerdown", "pointerup", "wheel", "dblclick"])
      el.addEventListener(event, (e) => e.stopPropagation());
    panels?.setGallery(el);
    selectedGallery = { el, entity, primed: false };
    return {
      target: new THREE.Vector3(
        entity.position.x,
        (entity.roofHeight || 0) + 1,
        entity.position.z,
      ),
      radius: Math.max(5, (entity.step || 3) * 1.5),
    };
  }

  function constrainCamera() {
    if (!topology) return;
    const bounded = boundedTarget(controls.target, navigationBounds),
      shift = new THREE.Vector3(
        bounded.x - controls.target.x,
        bounded.y - controls.target.y,
        bounded.z - controls.target.z,
      );
    controls.target.add(shift);
    camera.position.add(shift);
    const floor = Math.max(
      MIN_ALTITUDE,
      (urban?.roofHeight(camera.position.x, camera.position.z, 1.5) || 0) + 2.5,
    );
    if (camera.position.y < floor) camera.position.y = floor;
    const offset = camera.position.clone().sub(controls.target);
    if (offset.length() < controls.minDistance) {
      if (offset.lengthSq() < 0.001) offset.set(0.2, 0.8, 0.6);
      camera.position
        .copy(controls.target)
        .add(offset.setLength(controls.minDistance));
    }
    camera.lookAt(controls.target);
  }
  // Layout boxes ignore CSS transforms, so sliding panels count at their resting place.
  function layoutRect(el) {
    let x = 0,
      y = 0;
    for (let n = el; n; n = n.offsetParent) {
      x += n.offsetLeft;
      y += n.offsetTop;
    }
    return { x, y, width: el.offsetWidth, height: el.offsetHeight };
  }
  function displayed(el) {
    return (
      el.isConnected &&
      !el.hidden &&
      el.offsetWidth > 0 &&
      el.offsetHeight > 0 &&
      getComputedStyle(el).display !== "none"
    );
  }
  // The part of the stage that the interface leaves free; automatic views and labels share it.
  function measureFreeView() {
    const w = host.clientWidth,
      h = host.clientHeight,
      phone = w < 700;
    if (preview) {
      const inset = Math.round(Math.min(w, h) * 0.1);
      return {
        x: inset,
        y: inset,
        width: Math.max(20, w - inset * 2),
        height: Math.max(20, h - inset * 2),
      };
    }
    const side = phone ? 12 : 24,
      gap = phone ? 10 : 16,
      area = (b) => (b.right - b.x) * (b.bottom - b.y);
    let box = { x: side, y: side, right: w - side, bottom: h - side };
    const workspace = host.closest(".city-workspace");
    if (workspace) {
      const origin = layoutRect(host),
        selectors = immersive
          ? [".city-clean-button"]
          : [
              ".city-header",
              ".city-toolbar",
              ".city-clean-button",
              ".city-context",
              ".city-navigation",
            ];
      for (const selector of selectors)
        for (const el of workspace.querySelectorAll(selector)) {
          if (!displayed(el)) continue;
          const r = layoutRect(el),
            o = {
              x: r.x - origin.x,
              y: r.y - origin.y,
              right: r.x - origin.x + r.width,
              bottom: r.y - origin.y + r.height,
            };
          if (
            o.right <= box.x ||
            o.x >= box.right ||
            o.bottom <= box.y ||
            o.y >= box.bottom
          )
            continue;
          const cuts = [
            { ...box, y: o.bottom + gap },
            { ...box, bottom: o.y - gap },
            { ...box, x: o.right + gap },
            { ...box, right: o.x - gap },
          ].filter((c) => c.right - c.x >= 120 && c.bottom - c.y >= 120);
          if (cuts.length)
            box = cuts.reduce((a, b) => (area(b) > area(a) ? b : a));
        }
    }
    return {
      x: box.x,
      y: box.y,
      width: box.right - box.x,
      height: box.bottom - box.y,
    };
  }
  function freeViewport(force = false) {
    const now = performance.now(),
      key = [
        host.clientWidth,
        host.clientHeight,
        immersive,
        selection?.key,
        host.closest(".city-workspace")?.dataset.selection,
      ].join("|");
    if (force || !freeView || key !== freeViewKey || now - freeViewAt > 500) {
      freeView = measureFreeView();
      freeViewKey = key;
      freeViewAt = now;
    }
    return freeView;
  }
  // On wide screens the photo card covers the middle, so the building goes beside it.
  function besideGallery(viewport, card) {
    if (!card?.width) return viewport;
    const gap = 16,
      left = { ...viewport, width: card.x - gap - viewport.x },
      right = {
        ...viewport,
        x: card.x + card.width + gap,
        width: viewport.x + viewport.width - (card.x + card.width + gap),
      },
      best = right.width >= left.width ? right : left;
    return best.width >= 180 ? best : viewport;
  }
  // Fits the whole selection into the free viewport: the camera comes exactly as
  // close as that allows, never closer, and keeps the current compass heading.
  function frameSelection(animate = true) {
    const w = host.clientWidth,
      h = host.clientHeight;
    if (!topology || !selection || !w || !h) return null;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    let viewport = freeViewport(true);
    if(selection.kind==='root')viewport={x:viewport.x+16,y:viewport.y+16,width:Math.max(180,viewport.width-32),height:Math.max(180,viewport.height-32)};
    if (selection.kind === "product")
      viewport = besideGallery(viewport, panels?.stats()?.gallery);
    // A leaf with products is framed between its callout columns.
    else if (panels) viewport = panels.reserve(viewport);
    const offset = camera.position.clone().sub(controls.target),
      yaw =
        offset.x * offset.x + offset.z * offset.z > 1e-6
          ? Math.atan2(offset.x, offset.z)
          : 0.6,
      fit = fitCamera({
        node: selection,
        camera,
        width: w,
        height: h,
        viewport,
        bounds: navigationBounds,
        yaw,
        elevation: selection.kind==='root'?CITY_ELEVATION:FRAME_ELEVATION,
        productEntity: (p) => topology.byKey.get("product:" + p.id) || p,
        floorAt: (x, z) =>
          Math.max(MIN_ALTITUDE, (urban?.roofHeight(x, z, 1.5) || 0) + 2.5),
      });
    if (!fit) return null;
    camera.far=Math.max(camera.far,fit.far||camera.far);camera.updateProjectionMatrix();
    controls.maxDistance = Math.max(controls.maxDistance, fit.distance * 1.3);
    framing = {
      key: selection.key,
      viewport,
      points: fit.points,
      distance: fit.distance,
      complete: fit.complete,
    };
    zoomGoal = null;
    const intro=introPending && selection.kind==='root';
    if(intro)introPending=false;
    if (animate && motionOn()) {
      if(intro){
        const offset=fit.position.clone().sub(fit.target);
        offset.applyAxisAngle(new THREE.Vector3(0,1,0),-.52).multiplyScalar(1.18);
        offset.y=offset.y*1.42+20;
        camera.position.copy(fit.target).add(offset);
        controls.target.copy(fit.target);
      }
      tween = {
        kind: intro?'intro':"flight",
        from: camera.position.clone(),
        fromTarget: controls.target.clone(),
        to: fit.position.clone(),
        target: fit.target.clone(),
        start: performance.now(),
        duration: intro?3400:flightDuration(camera.position.distanceTo(fit.position)),
        clearance: 32,
      };
    } else {
      tween = null;
      camera.position.copy(fit.position);
      controls.target.copy(fit.target);
      camera.lookAt(controls.target);
    }
    dirty = true;
    return fit;
  }
  function setSelection(entity, { animate = true } = {}) {
    if (!topology) return;
    selection = entity || topology.root;
    removeGallery();
    if (selection.kind === "product") addGallery(selection);
    // Framing waits one frame, so the photo card and the footer are already laid out.
    autoFrame = true;
    pendingFrame = { animate };
    const b = navigationBounds;
    urban?.pulse(
      selection.kind === "root" && b
        ? new THREE.Vector3((b.minX + b.maxX) / 2, 0, (b.minZ + b.maxZ) / 2)
        : new THREE.Vector3(
            selection.position?.x || 0,
            0,
            selection.position?.z || 0,
          ),
      performance.now() / 1000,
    );
    dirty = true;
  }
  // Wheel and buttons move one distance goal; every frame approaches it, nothing restarts.
  function zoomBy(input) {
    if (!Number.isFinite(input) || !input) return;
    autoFrame = false;
    tween = null;
    const current = camera.position.distanceTo(controls.target);
    zoomGoal = zoomStep(
      zoomGoal ?? current,
      input,
      controls.minDistance,
      controls.maxDistance,
    );
    zoomAt = performance.now();
    fastUntil = zoomAt + 600;
    dirty = true;
  }
  function zoom(factor) {
    zoomBy(Math.log(factor) / Math.log(1.28));
  }
  const wheel = (e) => {
    if (preview) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    zoomBy(wheelInput(e.deltaY, e.deltaMode, host.clientHeight));
  };
  renderer.domElement.addEventListener("wheel", wheel, {
    passive: false,
    capture: true,
  });
  function hit(event) {
    const r = renderer.domElement.getBoundingClientRect();
    pointer.set(
      ((event.clientX - r.left) / r.width) * 2 - 1,
      (-(event.clientY - r.top) / r.height) * 2 + 1,
    );
    ray.setFromCamera(pointer, camera);
    const active = new Set(
      spatialStates.filter((s) => s.opacity > 0.5).map((s) => s.node.key),
    );
    return (
      ray.intersectObjects(
        colliders.filter((o) => active.has(o.userData.entity?.key)),
        false,
      )[0]?.object.userData.entity || null
    );
  }
  const pointerDown = (e) => {
      down = { x: e.clientX, y: e.clientY, time: performance.now() };
    },
    pointerUp = (e) => {
      if (
        down &&
        Math.hypot(e.clientX - down.x, e.clientY - down.y) < 6 &&
        performance.now() - down.time < 700 &&
        e.button === 0
      ) {
        const entity = hit(e);
        if (entity) onSelect?.(entity);
      }
      down = null;
    },
    pointerMove = (e) => {
      if (e.buttons || performance.now() - lastHoverHit < 45) return;
      lastHoverHit = performance.now();
      const entity = hit(e);
      renderer.domElement.style.cursor = entity ? "pointer" : "grab";
      const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0),
        point = new THREE.Vector3();
      urban?.hover(ray.ray.intersectPlane(plane, point));
      if (hover !== entity) {
        hover = entity;
        dirty = true;
      }
    },
    leave = () => {
      hover = null;
      urban?.hover(null);
      dirty = true;
    };
  renderer.domElement.addEventListener("pointerdown", pointerDown);
  renderer.domElement.addEventListener("pointerup", pointerUp);
  renderer.domElement.addEventListener("pointermove", pointerMove);
  renderer.domElement.addEventListener("pointerleave", leave);
  const controlStart = () => {
    tween = null;
    autoFrame = false;
    fastUntil = performance.now() + 900;
    dirty = true;
  };
  // Physical key codes work with Russian and English keyboard layouts.
  const flightKeys=new Set(),flightVelocity=new THREE.Vector3();let fastFlight=false;
  const editing=(el)=>!!el?.closest?.('input,textarea,select,[contenteditable="true"],[role="dialog"]');
  const clearFlight=()=>{flightKeys.clear();flightVelocity.set(0,0,0);fastFlight=false;};
  const keyDown=(event)=>{
    if(preview||event.ctrlKey||event.metaKey||event.altKey||document.hidden||editing(event.target))return;
    if(!['KeyW','KeyA','KeyS','KeyD'].includes(event.code))return;
    event.preventDefault();flightKeys.add(event.code);fastFlight=event.shiftKey;
    controlStart();pendingFrame=null;zoomGoal=null;
  };
  const keyUp=(event)=>{flightKeys.delete(event.code);if(event.key==='Shift')fastFlight=false;};
  const visibility=()=>{if(document.hidden)clearFlight();};
  if(!preview){window.addEventListener('keydown',keyDown);window.addEventListener('keyup',keyUp);window.addEventListener('blur',clearFlight);document.addEventListener('visibilitychange',visibility);}
  function keyboardFlight(dt){
    if(preview)return;
    if(editing(document.activeElement)){clearFlight();return;}
    const forward=camera.getWorldDirection(new THREE.Vector3());forward.y=0;forward.normalize();
    const right=new THREE.Vector3(-forward.z,0,forward.x),wish=new THREE.Vector3();
    if(flightKeys.has('KeyW'))wish.add(forward);if(flightKeys.has('KeyS'))wish.sub(forward);
    if(flightKeys.has('KeyD'))wish.add(right);if(flightKeys.has('KeyA'))wish.sub(right);
    const speed=Math.max(24,Math.min(110,camera.position.distanceTo(controls.target)*.45))*(fastFlight?2.2:1);
    if(wish.lengthSq())wish.normalize().multiplyScalar(speed);
    flightVelocity.lerp(wish,motionOn()?1-Math.exp(-12*dt):1);
    if(!flightKeys.size&&flightVelocity.lengthSq()<.01){flightVelocity.set(0,0,0);return;}
    const delta=flightVelocity.clone().multiplyScalar(dt);camera.position.add(delta);controls.target.add(delta);dirty=true;
  }
  controls.addEventListener("start", controlStart);
  controls.addEventListener("change", () => {
    fastUntil = Math.max(fastUntil, performance.now() + 250);
    dirty = true;
  });
  controls.addEventListener("end", () => {
    fastUntil = Math.max(fastUntil, performance.now() + 700);
  });
  const lost = (e) => {
    e.preventDefault();
    contextLost = true;
    onError?.(
      "Графический контекст потерян. Перезагрузите 3D-карту или откройте каталог.",
    );
  };
  renderer.domElement.addEventListener("webglcontextlost", lost);
  function resize() {
    const w = host.clientWidth,
      h = host.clientHeight;
    if (!w || !h) return;
    camera.aspect = w / h;
    if (topology)
      controls.maxDistance = Math.max(
        360,
        topology.root.radius * 5.5 * (camera.aspect < 0.8 ? 1.6 : 1),
        (framing?.distance || 0) * 1.3,
      );
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    if (autoFrame && topology && !pendingFrame) pendingFrame = { animate: false };
    freeView = null;
    dirty = true;
  }
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(host);
  const intersection = new IntersectionObserver((e) => {
    visible = e[0].isIntersecting;
    dirty = true;
  });
  intersection.observe(host);
  resize();
  function render(time) {
    if (!alive) return;
    frame = requestAnimationFrame(render);
    // Idle scenes render at ~30 fps; flights, zoom and dragging get every frame.
    const busy =
      Boolean(tween) ||
      flightKeys.size>0 || flightVelocity.lengthSq()>.01 ||
      zoomGoal !== null ||
      pendingFrame !== null ||
      time < fastUntil;
    if (
      document.hidden ||
      !visible ||
      contextLost ||
      (!busy && time - last < 32)
    )
      return;
    const dt = last ? Math.min(0.1, Math.max(0, (time - last) / 1000)) : 1 / 60;
    last = time;
    controls.enableDamping = motionOn() && !tween;
    controls.update();
    if (tween) {
      const t = tween.duration
          ? Math.max(0, Math.min(1, (time - tween.start) / tween.duration))
          : 1,
        e = flightEase(t);
      const pose = flightPose(tween.from, tween.to, t, tween.clearance);
      camera.position.set(pose.x, pose.y, pose.z);
      controls.target.lerpVectors(tween.fromTarget, tween.target, e);
      camera.lookAt(controls.target);
      dirty = true;
      if (t >= 1) tween = null;
    }
    if (zoomGoal !== null) {
      const offset = camera.position.clone().sub(controls.target);
      if (offset.lengthSq() < 0.001) offset.set(0.2, 0.8, 0.6);
      const next = motionOn()
          ? dampDistance(offset.length(), zoomGoal, dt)
          : zoomGoal,
        settled = Math.abs(next - zoomGoal) < Math.max(0.02, zoomGoal * 0.002);
      camera.position
        .copy(controls.target)
        .add(offset.setLength(settled ? zoomGoal : next));
      // Roofs may hold the camera above the goal; stop chasing it shortly after input ends.
      if (settled || time - zoomAt > 1500) zoomGoal = null;
      dirty = true;
    }
    keyboardFlight(dt);
    constrainCamera();
    if (urban && (dirty || motionOn()))
      urban.update(
        time / 1000,
        urbanLast ? Math.min(0.06, (time - urbanLast) / 1000) : 0,
        motionOn(),
      );
    urbanLast = time;
    camera.updateMatrixWorld();
    spatialStates =
      districts?.update({
        model: topology,
        camera,
        target: controls.target,
        selection,
        hover,
        bounds: panels ? panels.reserve(freeViewport()) : freeViewport(),
        avoid: panels ? panels.avoid() : null,
      }) || [];
    if (!preview) urban?.setDistrictDetail(spatialStates);
    const weights = new Map(spatialStates.map((s) => [s.node.key, s.opacity]));
    let areaAnimating = false;
    for (const node of topology?.nodes || []) {
      if (!node.visual) continue;
      const weight = preview
        ? node.depth === 0
          ? 1
          : 0
        : weights.get(node.key) || 0;
      const v = node.visual,
        targetOpacity = weight * 0.045,
        targetOutline = Math.min(
          1,
          weight * (node.depth ? 0.38 : 0.65) +
            (node.key === districts?.active() ||
            node === selection ||
            node === hover
              ? weight * 0.3
              : 0),
        ),
        alpha = motionOn() ? 0.18 : 1;
      if (
        Math.abs(v.material.opacity - targetOpacity) > 0.001 ||
        Math.abs(v.outline.material.opacity - targetOutline) > 0.001
      )
        areaAnimating = true;
      v.material.opacity += (targetOpacity - v.material.opacity) * alpha;
      v.outline.material.opacity +=
        (targetOutline - v.outline.material.opacity) * alpha;
      v.mesh.visible = v.material.opacity > 0.001;
      v.outline.visible = v.outline.material.opacity > 0.001;
    }
    if (areaAnimating) dirty = true;
    panels?.update({
      camera,
      model: topology,
      selection,
      quiet: immersive,
      distance: camera.position.distanceTo(controls.target),
      labelRects: districts?.rects() || [],
      hover,
    });
    if (pendingFrame && topology) {
      const request = pendingFrame;
      pendingFrame = null;
      frameSelection(request.animate);
    }
    for (const node of topology?.products || []) {
      if (node.visual)
        node.visual.mat.emissiveIntensity =
          node === hover || node === panelHover || node === selection
            ? 0.8
            : 0.12;
    }
    if (
      selectedGallery &&
      !selectedGallery.primed &&
      selectedGallery.el.isConnected
    ) {
      activateMedia(selectedGallery.el);
      selectedGallery.primed = true;
    }

    if (board?.userData.sweep)
      board.userData.sweep.uniforms.uTime.value = motionOn() ? time / 1000 : 0;
    if (dirty || motionOn()) {
      renderer.render(scene, camera);
      dirty = false;
    }
  }

  function framingState() {
    if (!framing) return null;
    const check = projectedFit(
      framing.points,
      camera,
      host.clientWidth,
      host.clientHeight,
      framing.viewport,
    );
    return {
      key: framing.key,
      inside: check.inside,
      complete: framing.complete,
      distance: framing.distance,
      viewport: framing.viewport,
      points: framing.points.length,
      outside: check.projected.filter((p) => !p.inside).slice(0, 8),
    };
  }
  frame = requestAnimationFrame(render);
  return {
    setData,
    setSelection,
    zoom,
    setImmersive(value) {
      const next = Boolean(value);
      if (next !== immersive && autoFrame && topology)
        pendingFrame = { animate: true };
      immersive = next;
      freeView = null;
      dirty = true;
    },
    home() {
      if (topology) {
        onSelect?.(topology.root);
      }
    },
    snapshot() {
      return {
        panels: panels?.stats(),
        districts: districts?.stats(),
        revision: THREE.REVISION,
        tweening: Boolean(tween),
        zooming: zoomGoal !== null,
        autoFrame,
        framing: framingState(),
        detailAreas: spatialStates
          .filter((s) => s.node.parent === selection && s.opacity > 0.99)
          .map((s) => s.node.key),
        expectedChildren: selection?.children?.map((n) => n.key) || [],
        city: urban?.stats(),
        immersive,
        selection: selection?.key,
        camera: camera.position.toArray(),
        navigation: {
          distance: camera.position.distanceTo(controls.target),
          min: controls.minDistance,
          max: controls.maxDistance,
          altitude: camera.position.y,
          clearance:
            urban?.roofHeight(camera.position.x, camera.position.z, 1.5) || 0,
        },
        target: controls.target.toArray(),
        renderedProducts: topology?.products.length || 0,
        photosVisible: panels?.stats().cards?.length || 0,
        graphics: contextLost ? "lost" : "webgl2",
        pickPoints: (topology?.root.children || []).map((node) => {
          const v = new THREE.Vector3(
            node.position.x,
            0,
            node.position.z,
          ).project(camera);
          return {
            key: node.key,
            x: (v.x * 0.5 + 0.5) * host.clientWidth,
            y: (-0.5 * v.y + 0.5) * host.clientHeight,
          };
        }),
      };
    },
    destroy() {
      alive = false;
      cancelAnimationFrame(frame);
      resizeObserver.disconnect();
      intersection.disconnect();
      clearFlight();window.removeEventListener('keydown',keyDown);window.removeEventListener('keyup',keyUp);window.removeEventListener('blur',clearFlight);document.removeEventListener('visibilitychange',visibility);
      controls.dispose();
      clearWorld();
      panels?.destroy();
      districts?.destroy();
      renderer.domElement.removeEventListener("webglcontextlost", lost);
      renderer.domElement.removeEventListener("pointerdown", pointerDown);
      renderer.domElement.removeEventListener("pointerup", pointerUp);
      renderer.domElement.removeEventListener("pointermove", pointerMove);
      renderer.domElement.removeEventListener("pointerleave", leave);
      renderer.domElement.removeEventListener("wheel", wheel, true);
      renderer.renderLists.dispose();
      renderer.domElement.remove();
      if (contextLost) {
        renderer.dispose();
        renderer.forceContextLoss();
      } else {
        renderer.setSize(2, 2);
        parkedRenderer = renderer;
      }
    },
  };
}
