import { h } from "./dom.js";
import { get, post } from "./api.js";
import { motionOn } from "./motion.js";
import { on } from "./state.js";
const metaCache = new Map(),
  imageCache = new Map(),
  pendingObservers = new Map();
on("data", () => {
  metaCache.clear();
  imageCache.clear();
});
new MutationObserver((records) => {
  for (const record of records)
    for (const node of record.removedNodes) {
      if (node.nodeType !== 1) continue;
      for (const video of [node, ...node.querySelectorAll("video")].filter(
        (n) => n.tagName === "VIDEO",
      )) {
        if (!video.isConnected) {
          video.pause();
          video.removeAttribute("src");
          video.load();
        }
      }
    }
  for (const [target, observer] of pendingObservers) {
    if (!target.isConnected) {
      observer.disconnect();
      pendingObservers.delete(target);
    }
  }
}).observe(document.documentElement, { childList: true, subtree: true });
document.addEventListener(
  "play",
  (e) => {
    if (e.target.tagName === "VIDEO")
      document.querySelectorAll("video").forEach((v) => {
        if (v !== e.target) v.pause();
      });
  },
  true,
);
const baseFor = (p) =>
  p.media_url ||
  (p.inbox_id
    ? `/api/inbox/${p.inbox_id}/media`
    : `/api/products/${p.id || p.product_id}/media`);
async function metadata(p) {
  if (p.media?.items) return p.media.items.map((v, index) => ({ ...v, index }));
  if (p.image_data)
    return [{ index: 0, type: "image", image_data: p.image_data }];
  const base = baseFor(p);
  if (!metaCache.has(base)) {
    if (metaCache.size > 120) metaCache.delete(metaCache.keys().next().value);
    metaCache.set(
      base,
      get(base)
        .then((d) => d.items || [])
        .catch(() => []),
    );
  }
  return metaCache.get(base);
}
async function asset(p, item) {
  if (item.image_data || item.type === "video") return item;
  const key = `${baseFor(p)}/${item.index}`;
  if (!imageCache.has(key)) {
    if (imageCache.size >= 45)
      imageCache.delete(imageCache.keys().next().value);
    imageCache.set(
      key,
      get(key).catch(() => null),
    );
  }
  return imageCache.get(key);
}
export function invalidateMedia(p) {
  metaCache.delete(baseFor(p));
  for (const k of imageCache.keys())
    if (k.startsWith(baseFor(p) + "/")) imageCache.delete(k);
}
export function productImage(p, compact = false) {
  return gallery(p, { compact });
}
export function photoGallery(p) {
  return gallery(p, { expanded: true });
}
function gallery(
  p,
  { compact = false, expanded = false, zoomed = false } = {},
) {
  const box = h("div", {
      class: [
        "product-photo",
        compact && "compact",
        expanded && "gallery-expanded",
      ],
      role: "group",
      "aria-label": `Галерея: ${p.title || "товар"}`,
    }),
    stage = h("div", {
      class: "gallery-stage",
      tabindex: "0",
      "aria-label": "Фото товара. Стрелки влево и вправо — перелистывание",
    }),
    empty = h("span", { class: "photo-empty", text: "Загрузка фото…" }),
    track = h("div", { class: "gallery-track" }),
    counter = h("span", { class: "gallery-count", "aria-live": "polite" }),
    thumbs = h("div", {
      class: "gallery-thumbnails",
      "aria-label": "Выбор фото или видео",
    });
  let items = [],
    at = 0,
    token = 0,
    loaded = false,
    touchX = null;
  const stop = (e) => {
    e.stopPropagation();
  };
  const prev = h("button", {
      class: "photo-nav previous",
      type: "button",
      "aria-label": "Предыдущее фото",
      text: "‹",
      onClick: (e) => {
        stop(e);
        show(at - 1);
      },
    }),
    next = h("button", {
      class: "photo-nav next",
      type: "button",
      "aria-label": "Следующее фото",
      text: "›",
      onClick: (e) => {
        stop(e);
        show(at + 1);
      },
    });
  stage.append(empty, track, prev, next, counter);
  box.append(stage);
  if (expanded) box.append(thumbs);
  stage.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      stop(e);
      show(at + (e.key === "ArrowRight" ? 1 : -1));
    }
  });
  stage.addEventListener("pointerdown", (e) => {
    touchX = e.clientX;
  });
  stage.addEventListener("pointerup", (e) => {
    if (touchX !== null && Math.abs(e.clientX - touchX) > 45) {
      stop(e);
      show(at + (e.clientX < touchX ? 1 : -1));
    }
    touchX = null;
  });
  async function show(index) {
    if (!items.length) return;
    const old = at;
    at = (index + items.length) % items.length;
    const mine = ++token,
      item = items[at];
    counter.textContent = `${at + 1} / ${items.length}`;
    prev.hidden = next.hidden = items.length < 2;
    [...thumbs.children].forEach((el, i) => {
      el.classList.toggle("active", i === at);
      el.setAttribute("aria-pressed", String(i === at));
    });
    const data = await asset(p, item);
    if (mine !== token || !box.isConnected) return;
    const layer = h("div", { class: "gallery-layer" });
    if (!data) {
      layer.append(
        h("span", {
          class: "photo-empty",
          text: "Фото не загрузилось. Обновите галерею через Eye.",
        }),
      );
    } else if (data.type === "video") {
      const video = h("video", {
        controls: true,
        preload: "none",
        playsinline: true,
        referrerpolicy: "no-referrer",
        "aria-label": "Видео товара",
        src: data.source_url,
      });
      video.addEventListener("error", () => {
        layer.append(
          h("span", {
            class: "media-error",
            text: "Видео недоступно или ссылка истекла. Откройте оригинал в магазине.",
          }),
        );
      });
      layer.append(video);
    } else {
      const img = h("img", {
        alt: p.title || "Фото товара",
        decoding: "async",
        referrerpolicy: "no-referrer",
      });
      img.src = data.image_data || data.source_url || "";
      img.addEventListener("error", () => {
        img.remove();
        layer.append(
          h("span", {
            class: "photo-empty",
            text: "Фото недоступно. Обновите товар через Eye.",
          }),
        );
      });
      layer.append(img);
    }
    const previous = track.lastElementChild;
    for (const stale of [...track.children])
      if (stale !== previous) {
        stale.querySelectorAll("video").forEach((v) => {
          v.pause();
          v.removeAttribute("src");
          v.load();
        });
        stale.remove();
      }
    track.append(layer);
    empty.hidden = true;
    if (previous) {
      previous.querySelectorAll("video").forEach((v) => {
        v.pause();
        v.removeAttribute("src");
        v.load();
        v.remove();
      });
      if (motionOn() && old !== at) {
        const dir = index >= old ? 1 : -1;
        layer.animate(
          [
            { opacity: 0, transform: `translateX(${dir * 18}%)` },
            { opacity: 1, transform: "translateX(0)" },
          ],
          { duration: 260, easing: "cubic-bezier(.2,.8,.2,1)" },
        );
        previous
          .animate(
            [
              { opacity: 1, transform: "translateX(0)" },
              { opacity: 0, transform: `translateX(${-dir * 15}%)` },
            ],
            { duration: 220 },
          )
          .finished.catch(() => {})
          .then(() => previous.remove());
      } else previous.remove();
    }
  }
  async function init() {
    if (loaded || !box.isConnected) return;
    loaded = true;
    const raw = await metadata(p);
    if (!box.isConnected) return;
    items = expanded ? raw : raw.filter((i) => i.type === "image");
    if (!items.length) {
      empty.textContent = "Фото ещё не импортированы";
      counter.hidden = prev.hidden = next.hidden = true;
      if (expanded) {
        empty.append(
          h("small", {
            text: "Откройте исходную карточку, соберите галерею в Eye и примите новый файл во «Входящих».",
          }),
        );
      }
      return;
    }
    if (expanded) {
      for (const [i, item] of items.entries()) {
        const button = h(
          "button",
          {
            class: "gallery-thumb",
            type: "button",
            "aria-label": `${item.type === "video" ? "Видео" : "Фото"} ${i + 1}`,
            "aria-pressed": "false",
            onClick: (e) => {
              stop(e);
              show(i);
            },
          },
          h("span", { text: item.type === "video" ? "▶" : String(i + 1) }),
        );
        thumbs.append(button);
        if (item.type === "image")
          asset(p, item).then((d) => {
            if (!d || !button.isConnected) return;
            const image = h("img", {
              src: d.image_data || d.source_url,
              alt: `Фото ${i + 1}`,
              loading: "lazy",
              referrerpolicy: "no-referrer",
            });
            image.addEventListener("error", () => image.remove());
            button.prepend(image);
          });
      }
    }
    await show(0);
  }
  prev.hidden = next.hidden = true;
  box.addEventListener("r52-media-visible", () => {
    observer.disconnect();
    pendingObservers.delete(box);
    init();
  });
  const observer = new IntersectionObserver(
    (es) => {
      if (es.some((e) => e.isIntersecting)) {
        observer.disconnect();
        pendingObservers.delete(box);
        init();
      }
    },
    { rootMargin: "150px" },
  );
  observer.observe(box);
  pendingObservers.set(box, observer);
  if (expanded && !zoomed)
    stage.addEventListener("click", (e) => {
      if (
        e.target.closest("button,video") ||
        !items.length ||
        items[at]?.type === "video"
      )
        return;
      let dialog;
      const dispose = () => {
        if (!dialog?.isConnected) return;
        dialog.querySelectorAll("video").forEach((v) => {
          v.pause();
          v.removeAttribute("src");
          v.load();
        });
        dialog.remove();
        if (stage.isConnected) stage.focus({ preventScroll: true });
      };
      const close = () => {
        if (dialog?.open) dialog.close();
        dispose();
      };
      dialog = h(
        "dialog",
        { class: "photo-zoom gallery-zoom", "aria-label": "Просмотр фото" },
        h("button", {
          class: "icon-btn",
          type: "button",
          "aria-label": "Закрыть фото",
          text: "×",
          onClick: close,
        }),
        gallery(p, { expanded: true, zoomed: true }),
      );
      dialog.addEventListener("close", dispose);
      dialog.addEventListener("cancel", (event) => {
        event.preventDefault();
        close();
      });
      document.body.append(dialog);
      dialog.showModal();
      activateMedia(dialog);
    });
  return box;
}
export async function attachPhoto(p) {
  const input = h("input", {
    type: "file",
    multiple: true,
    accept: "image/jpeg,image/png,image/webp",
  });
  input.addEventListener("change", async () => {
    try {
      const items = [];
      for (const file of [...input.files].slice(0, 40)) {
        if (file.size > 15000000)
          throw Error("Один файл должен быть не больше 15 МБ");
        const bmp = await createImageBitmap(file);
        if (bmp.width * bmp.height > 40000000) {
          bmp.close();
          throw Error("Слишком большое разрешение");
        }
        const k = Math.min(1, 1100 / Math.max(bmp.width, bmp.height)),
          c = document.createElement("canvas");
        c.width = Math.round(bmp.width * k);
        c.height = Math.round(bmp.height * k);
        const ctx = c.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, c.width, c.height);
        ctx.drawImage(bmp, 0, 0, c.width, c.height);
        bmp.close();
        let data = c.toDataURL("image/jpeg", 0.78);
        if (data.length > 550000) data = c.toDataURL("image/jpeg", 0.48);
        if (data.length > 550000)
          throw Error("Фото слишком большое после сжатия");
        items.push({ type: "image", image_data: data });
      }
      if (!items.length) return;
      await post(`/api/products/${p.id}/media`, { items });
      invalidateMedia(p);
      location.reload();
    } catch (e) {
      alert(e.message);
    }
  });
  input.click();
}
export async function setCategory(p) {
  const path = prompt(
    "Путь категории: Техника > Видеокарты",
    p.category_path || "",
  );
  if (!path?.trim()) return;
  try {
    await post(`/api/products/${p.id}/category`, { category_path: path });
    location.reload();
  } catch (e) {
    alert(e.message);
  }
}

// CSS3D attaches its DOM at render time, after normal lazy observers may be cleaned up.
export function activateMedia(root) {
  root
    .querySelectorAll(".product-photo")
    .forEach((box) => box.dispatchEvent(new Event("r52-media-visible")));
}
