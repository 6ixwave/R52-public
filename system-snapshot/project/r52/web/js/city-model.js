import { createTerritories, contains } from "./city-territories.js";
import { CATEGORY_GROUPS, cityPath } from "./city-taxonomy.js";
// Deterministic topology of the real local catalogue; no generated offers.
const RULES = [
  [/rtx|gtx|radeon|видеокарт|geforce/i, ["Техника", "Видеокарты"]],
  [/ddr[345]|оперативн.*памят/i, ["Техника", "Оперативная память"]],
  [/ноутбук|laptop|macbook/i, ["Техника", "Ноутбуки"]],
  [/смартфон|iphone|телефон|планшет/i, ["Техника", "Смартфоны и планшеты"]],
  [
    /заряд|power.?bank|аккумулятор|сетевой фильтр/i,
    ["Техника", "Питание и аксессуары"],
  ],
  [
    /компьютер|процессор|ssd|монитор|наушник/i,
    ["Техника", "Компьютеры и периферия"],
  ],
  [
    /жалюзи|штор|ков[её]р|интерьер|матрас|мебель/i,
    ["Дом и ремонт", "Интерьер"],
  ],
  [/куртк|ботин|обув|пуховик|одежд|джинс/i, ["Одежда и обувь"]],
  [/книг|стих|игруш|детск/i, ["Детские товары"]],
  [/трактор|авто|шины/i, ["Транспорт", "Техника и запчасти"]],
];
export function categoryFor(p) {
  const a = String(p.category_path || "")
    .split(/\s*>\s*/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 5);
  return a.length
    ? a
    : RULES.find(([rule]) => rule.test(p.title || ""))?.[1] || ["Прочее"];
}
export const COLORS = [
  "#9b79ee",
  "#43cbb2",
  "#edac65",
  "#5dabe8",
  "#e17ea8",
  "#a5c765",
  "#b48ce6",
];
export function districtColor(index, depth = 0) {
  return depth
    ? `hsl(${(index * 137.508 + depth * 49 + 190) % 360}, 62%, 64%)`
    : COLORS[index % COLORS.length];
}
export function regionAt(model, x, z) {
  return (
    model.nodes
      .filter(
        (n) =>
          n.kind === "area" &&
          (n.polygon
            ? contains(n.polygon, x, z)
            : Math.abs(x - n.position.x) <= n.radius &&
              Math.abs(z - n.position.z) <= n.radius),
      )
      .sort((a, b) => b.depth - a.depth)[0] || null
  );
}
export function hexSlots(n) {
  if (!n) return [];
  const ring = Math.ceil(Math.sqrt(n)) + 1,
    points = [];
  for (let q = -ring; q <= ring; q++)
    for (let r = -ring; r <= ring; r++) {
      const x = Math.sqrt(3) * (q + r / 2),
        z = 1.5 * r;
      points.push({ x, z, d: x * x + z * z });
    }
  points.sort((a, b) => a.d - b.d || a.z - b.z || a.x - b.x);
  const selected = points.slice(0, n),
    cx = selected.reduce((s, p) => s + p.x, 0) / n,
    cz = selected.reduce((s, p) => s + p.z, 0) / n;
  return selected.map((p) => ({ x: p.x - cx, z: p.z - cz }));
}
export function buildTopology(input, { includeEmpty = false } = {}) {
  const root = {
      key: "root",
      kind: "root",
      name: "Город предложений",
      path: [],
      children: [],
      products: [],
      position: { x: 0, z: 0 },
      radius: 20,
    },
    nodes = [root],
    byKey = new Map([["root", root]]),
    seen = new Set();
  if (includeEmpty)
    for (const group of CATEGORY_GROUPS) {
      let parent = root;
      for (const name of [group.name]) {
        const key = "area:" + name;
        const node = {
          key,
          kind: "area",
          name,
          path: [name],
          parent: root,
          children: [],
          products: [],
          depth: 0,
          position: { x: 0, z: 0 },
          radius: 1,
          planned: true,
        };
        root.children.push(node);
        nodes.push(node);
        byKey.set(key, node);
        parent = node;
      }
      for (const name of group.children) {
        const path = [group.name, name],
          key = "area:" + path.join("\u001f");
        const node = {
          key,
          kind: "area",
          name,
          path,
          parent,
          children: [],
          products: [],
          depth: 1,
          position: { x: 0, z: 0 },
          radius: 1,
          planned: true,
        };
        parent.children.push(node);
        nodes.push(node);
        byKey.set(key, node);
      }
    }
  for (const product of input) {
    if (product.id == null || seen.has(product.id)) continue;
    seen.add(product.id);
    root.products.push(product);
    let parent = root;
    for (const [depth, name] of (includeEmpty
      ? cityPath(categoryFor(product), product)
      : categoryFor(product)
    ).entries()) {
      const path = [...parent.path, name],
        key = "area:" + path.join("\u001f");
      let node = byKey.get(key);
      if (!node) {
        node = {
          key,
          kind: "area",
          name,
          path,
          parent,
          children: [],
          products: [],
          depth,
          position: { x: 0, z: 0 },
          radius: 1,
        };
        parent.children.push(node);
        nodes.push(node);
        byKey.set(key, node);
      }
      node.products.push(product);
      parent = node;
    }
  }
  function sort(node) {
    node.children.sort((a, b) => {
      if (includeEmpty && node === root) {
        const ai = CATEGORY_GROUPS.findIndex((g) => g.name === a.name),
          bi = CATEGORY_GROUPS.findIndex((g) => g.name === b.name);
        return (
          (ai < 0 ? 999 : ai) - (bi < 0 ? 999 : bi) ||
          a.name.localeCompare(b.name, "ru")
        );
      }
      return a.name.localeCompare(b.name, "ru");
    });
    node.children.forEach(sort);
  }
  sort(root);
  const cols = includeEmpty
      ? 6
      : Math.ceil(Math.sqrt(root.children.length || 1)),
    rows = includeEmpty
      ? Math.ceil(CATEGORY_GROUPS.length / cols)
      : Math.ceil(root.children.length / cols);
  root.children.forEach((node, i) => {
    node.color = includeEmpty
      ? `hsl(${(i * 137.508 + 215) % 360}, 54%, 62%)`
      : COLORS[i % COLORS.length];
    node.radius = 18;
    node.position = {
      x: ((i % cols) - (cols - 1) / 2) * 36 + (cols % 2 ? 0 : 6),
      z: (Math.floor(i / cols) - (rows - 1) / 2) * 36 + (rows % 2 ? 0 : 6),
    };
    layout(node);
  });
  function layout(node) {
    if (!node.children.length) return;
    const cols = Math.ceil(Math.sqrt(node.children.length)),
      rows = Math.ceil(node.children.length / cols),
      subRadius =
        node.children.length === 1
          ? node.radius * 0.86
          : (node.radius * 0.7) / Math.max(cols, rows),
      step = subRadius * 2.04;
    node.children.forEach((child, i) => {
      child.color = districtColor(i + nodes.indexOf(node) * 3, child.depth);
      child.radius = subRadius;
      child.position = {
        x: node.position.x + ((i % cols) - (cols - 1) / 2) * step,
        z: node.position.z + (Math.floor(i / cols) - (rows - 1) / 2) * step,
      };
      layout(child);
    });
  }

  if (includeEmpty) createTerritories(root);
  const products = [];
  for (const node of nodes.filter(
    (n) => n.kind === "area" && !n.children.length,
  )) {
    const shown = node.products.slice(0, Math.max(0,Math.min(24,600-products.length))),
      cols = Math.ceil(Math.sqrt(shown.length || 1)),
      rows = Math.ceil(shown.length / cols),
      step = (node.radius * 1.16) / Math.max(cols, rows, 1);
    node.step = step;
    node.hiddenCount = node.products.length - shown.length;
    shown.forEach((p, i) => {
      const entity = {
        key: "product:" + p.id,
        kind: "product",
        name: p.title,
        product: p,
        parent: node,
        path: node.path,
        color: node.color,
        radius: Math.max(0.3, step * 0.75),
        step,
        position: {
          x: node.position.x + ((i % cols) - (cols - 1) / 2) * step,
          z: node.position.z + (Math.floor(i / cols) - (rows - 1) / 2) * step,
        },
        cardScale: Math.min(0.028, (step * 0.72) / 210),
      };
      products.push(entity);
      byKey.set(entity.key, entity);
    });
  }
  root.radius =
    Math.max(
      54,
      ...root.children.map(
        (n) => Math.hypot(n.position.x, n.position.z) + n.radius,
      ),
    ) + 4;
  // Where a leaf has room, offers occupy real lots beside streets, not the roadway.
  for (const leaf of nodes.filter(
    (n) => n.kind === "area" && !n.children.length,
  )) {
    const offers = products.filter((p) => p.parent === leaf),
      lots = [];
    for (
      let ix = Math.floor((leaf.position.x - leaf.radius) / 12) - 1;
      ix <= Math.ceil((leaf.position.x + leaf.radius) / 12) + 1;
      ix++
    )
      for (
        let iz = Math.floor((leaf.position.z - leaf.radius) / 12) - 1;
        iz <= Math.ceil((leaf.position.z + leaf.radius) / 12) + 1;
        iz++
      )
        for (const dx of [-2.6, 2.6])
          for (const dz of [-2.6, 2.6]) {
            const x = ix * 12 + dx,
              z = iz * 12 + dz;
            if (
              leaf.polygon
                ? contains(leaf.polygon, x, z) &&
                  Math.hypot(x - leaf.position.x, z - leaf.position.z) <
                    leaf.radius * 0.84
                : Math.hypot(x - leaf.position.x, z - leaf.position.z) <
                  leaf.radius * 0.84
            )
              lots.push({ x, z });
          }
    if (lots.length >= offers.length)
      for (const offer of offers) {
        lots.sort(
          (a, b) =>
            Math.hypot(a.x - offer.position.x, a.z - offer.position.z) -
              Math.hypot(b.x - offer.position.x, b.z - offer.position.z) ||
            a.x - b.x ||
            a.z - b.z,
        );
        offer.position = lots.shift();
      }
  }
  // Preserve addressable selections beyond the decorative geometry budget.
  for (const leaf of nodes.filter(
    (n) => n.kind === "area" && !n.children.length,
  ))
    for (const product of leaf.products) {
      const key = "product:" + product.id;
      if (byKey.has(key)) continue;
      byKey.set(key, {
        key,
        kind: "product",
        name: product.title,
        product,
        parent: leaf,
        path: leaf.path,
        color: leaf.color,
        radius: 2,
        step: 3,
        position: { ...leaf.position },
        roofHeight: 0,
        virtual: true,
      });
    }
  for (const node of nodes) node.empty = !node.products.length;
  return {
    root,
    nodes,
    products,
    byKey,
    total: root.products.length,
    includeEmpty,
  };
}
export function contextIds(entity) {
  return (entity.kind === "product" ? [entity.product] : entity.products || [])
    .slice(0, 12)
    .map((p) => p.id);
}
export function contextPrompt(entity, filters = {}) {
  const total = (
    entity.kind === "product" ? [entity.product] : entity.products || []
  ).length;
  return `Сравни сохранённые сведения по выбранным на карте товарам. Область: ${(entity.path?.join(" > ") || "Весь город").slice(0, 140)}. ${entity.kind === "product" ? entity.name.slice(0, 100) + ". " : ""}${total > 12 ? "Переданы первые 12 из " + total + " записей. " : ""}${filters.source ? "Площадка: " + filters.source + ". " : ""}Обозначь пробелы и условия цен. Не придумывай актуальное наличие, доставку и гарантию.`.slice(
    0,
    490,
  );
}
