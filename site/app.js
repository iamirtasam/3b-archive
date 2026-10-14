/* 3B ARCHIVE â€” front-of-house logic
   One origin: the API, the site, and the videos share a domain,
   so session + CloudFront cookies ride on every fetch automatically. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const views = {
    login: $("login-view"),
    app: $("app-view"),
    player: $("player-view"),
    upload: $("upload-view"),
  };

  const state = { videos: [], username: null, folder: null, hls: null };

  /* ---------------- API (same-origin, credentials included) ------------- */

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      method: opts.method || (opts.body ? "POST" : "GET"),
      credentials: "same-origin",
      headers: opts.body ? { "content-type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = {};
    try { data = await res.json(); } catch { /* empty body is fine */ }
    return { status: res.status, data };
  }

  /* ---------------- view switching -------------------------------------- */

  function show(name) {
    for (const [key, el] of Object.entries(views)) {
      el.hidden = key !== name;
    }
    window.scrollTo(0, 0);
  }

  /* ---------------- formatting ------------------------------------------ */

  const fmtMin = (s) => {
    const m = Math.round(s / 60);
    return m >= 60 ? `${(m / 60).toFixed(1)} HRS` : `${m} MIN`;
  };
  const totalHours = (list) =>
    `${(list.reduce((a, v) => a + v.duration_seconds, 0) / 3600).toFixed(1)} HOURS`;

  /* ---------------- archive management ----------------------------------- */

  // One shared confirm flow for destructive/renaming actions. Returns the
  // response of fn() after the user accepts, or undefined if cancelled.
  async function manage(action, video) {
    const v = video;
    if (action === "delete") {
      if (!confirm(`DELETE "${v.title}"?\n\nThe video and all its files are removed from S3 permanently.`)) return;
      const r = await api("/api/videos/delete", { body: { slug: v.slug } });
      if (r.status === 200) {
        state.videos = state.videos.filter((x) => x.slug !== v.slug);
        refreshAfterManage();
      } else alert((r.data.error || "delete failed").toUpperCase());
      return;
    }
    if (action === "rename") {
      const t = prompt("NEW TITLE:", v.title);
      if (!t || t.trim() === v.title) return;
      const r = await api("/api/videos/rename", { body: { slug: v.slug, title: t.trim() } });
      if (r.status === 200) {
        v.title = t.trim();
        refreshAfterManage();
      } else alert((r.data.error || "rename failed").toUpperCase());
      return;
    }
    if (action === "move") {
      const f = prompt(`MOVE "${v.title}" TO FOLDER:\n(existing folders: ${[...new Set(state.videos.map((x) => x.folder))].join(", ")})`, v.folder);
      if (!f || !f.trim() || f.trim() === v.folder) return;
      const r = await api("/api/videos/move", { body: { slug: v.slug, folder: f.trim() } });
      if (r.status === 200) {
        v.folder = f.trim();
        refreshAfterManage();
      } else alert((r.data.error || "move failed").toUpperCase());
      return;
    }
  }

  function refreshAfterManage() {
    $("hero-count").textContent = `${state.videos.length} TITLES`;
    $("hero-hours").textContent = totalHours(state.videos);
    renderFolderNav();
    renderFolders();
    renderGrid();
    renderTicker();
  }

  /* ---------------- folders + grids ------------------------------------- */

  const byFolder = () => {
    const map = new Map();
    for (const v of state.videos) {
      if (!map.has(v.folder)) map.set(v.folder, []);
      map.get(v.folder).push(v);
    }
    return map;
  };

  function renderFolderNav() {
    const nav = $("folder-nav");
    nav.textContent = "";
    const mk = (label, hash, active) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.dataset.nav = "";
      b.dataset.hash = hash;
      b.className = active ? "active" : "";
      b.onclick = () => { location.hash = hash; };
      nav.appendChild(b);
    };
    mk("ALL", "#/", state.folder === null);
    for (const [folder, list] of byFolder()) {
      mk(`${folder.toUpperCase()} (${list.length})`,
         `#/folder/${encodeURIComponent(folder)}`,
         state.folder === folder);
    }
  }

  function renderFolders() {
    const grid = $("folder-grid");
    grid.textContent = "";
    grid.className = "folder-grid stagger";
    for (const [folder, list] of byFolder()) {
      const card = document.createElement("button");
      card.className = "folder-card";
      card.innerHTML = `
        <div>
          <span class="mint-tag">${list.length} ${list.length === 1 ? "TITLE" : "TITLES"}</span>
          <span class="folder-name">${folder}</span>
          <span class="mono-label">${fmtMin(list.reduce((a, v) => a + v.duration_seconds, 0))} OF FOOTAGE</span>
        </div>
        <span class="folder-arrow">&rarr;</span>
        <span class="card-menu" title="manage folder">⋯</span>`;
      card.onclick = (e) => {
        if (e.target.closest(".card-menu")) {
          folderMenu(e, folder);
          return;
        }
        location.hash = `#/folder/${encodeURIComponent(folder)}`;
      };
      grid.appendChild(card);
    }
  }

  async function folderMenu(e, folder) {
    e.stopPropagation();
    document.querySelector(".manage-menu")?.remove();
    const menu = document.createElement("div");
    menu.className = "manage-menu";
    menu.innerHTML = `
      <button data-act="rename">RENAME FOLDER</button>
      <button data-act="delete" class="danger">DELETE FOLDER VIDEOS…</button>`;
    menu.style.left = `${Math.min(e.clientX, window.innerWidth - 190)}px`;
    menu.style.top = `${e.clientY}px`;
    menu.addEventListener("click", async (ev) => {
      const act = ev.target.dataset.act;
      menu.remove();
      if (act === "rename") {
        const to = prompt(`RENAME FOLDER "${folder}" TO:`, folder);
        if (!to || to.trim() === folder) return;
        const r = await api("/api/folders/rename", { body: { from: folder, to: to.trim() } });
        if (r.status === 200) {
          const fresh = await api("/api/videos");
          if (fresh.status === 200) { state.videos = fresh.data.videos; refreshAfterManage(); }
        } else alert((r.data.error || "rename failed").toUpperCase());
      } else if (act === "delete") {
        if (!confirm(`DELETE EVERY VIDEO IN "${folder}"?\n\nAll files are removed from S3 permanently.`)) return;
        const inFolder = state.videos.filter((v) => v.folder === folder);
        for (const v of inFolder) {
          await api("/api/videos/delete", { body: { slug: v.slug } });
        }
        const fresh = await api("/api/videos");
        if (fresh.status === 200) { state.videos = fresh.data.videos; refreshAfterManage(); }
      }
    });
    document.body.appendChild(menu);
    setTimeout(() => {
      document.addEventListener("click", function close(ev) {
        if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener("click", close); }
      });
    }, 0);
  }

  function renderGrid() {
    const list = state.folder === null
      ? state.videos
      : state.videos.filter((v) => v.folder === state.folder);

    $("grid-title").textContent = state.folder ? state.folder.toUpperCase() : "EVERYTHING";
    $("grid-count").textContent = `${list.length} ${list.length === 1 ? "TITLE" : "TITLES"}`;

    const grid = $("video-grid");
    grid.textContent = "";
    grid.className = "video-grid stagger";
    for (const v of list) {
      const card = document.createElement("button");
      card.className = "video-card";
      const resume = Number(localStorage.getItem(`resume:${v.slug}`) || 0);
      const pct = resume && v.duration_seconds
        ? Math.min(100, Math.round((resume / v.duration_seconds) * 100)) : 0;
      card.innerHTML = `
        <div class="video-thumb">
          <img src="${v.thumbnail}" alt="" loading="lazy">
          <span class="thumb-tag">${fmtMin(v.duration_seconds)}</span>
          <span class="card-menu" title="manage">⋯</span>
        </div>
        <span class="video-title">${v.title}</span>
        <div class="video-meta">
          <span class="mono-label">${v.folder.toUpperCase()}</span>
          ${pct ? `<span class="mono-label">${pct}% WATCHED</span>` :
                  `<span class="mono-label">${v.height ? `${v.height}P` : "HLS"}</span>`}
        </div>
        ${pct ? `<div class="video-progress"><div style="width:${pct}%"></div></div>` : ""}`;
      card.onclick = (e) => {
        if (e.target.closest(".card-menu")) {
          manageMenu(e, v);
          return;
        }
        location.hash = `#/watch/${v.slug}`;
      };
      grid.appendChild(card);
    }
    $("all-folders-btn").hidden = state.folder === null;
    $("folder-section").hidden = state.folder !== null;
  }

  // Floating action menu per card: rename / move / delete.
  function manageMenu(e, v) {
    e.stopPropagation();
    document.querySelector(".manage-menu")?.remove();
    const menu = document.createElement("div");
    menu.className = "manage-menu";
    menu.innerHTML = `
      <button data-act="rename">RENAME</button>
      <button data-act="move">MOVE TO FOLDER</button>
      <button data-act="delete" class="danger">DELETE</button>`;
    menu.style.left = `${Math.min(e.clientX, window.innerWidth - 190)}px`;
    menu.style.top = `${e.clientY}px`;
    menu.addEventListener("click", async (ev) => {
      const act = ev.target.dataset.act;
      menu.remove();
      if (act) await manage(act, v);
    });
    document.body.appendChild(menu);
    setTimeout(() => {
      document.addEventListener("click", function close(ev) {
        if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener("click", close); }
      });
    }, 0);
  }

  function renderTicker() {
    const titles = state.videos.slice(0, 8)
      .map((v) => v.title.toUpperCase()).join(" â€” ");
    const text = `NOW STREAMING â€” ${titles} â€” NOW STREAMING â€” ${titles} â€” `;
    for (const id of ["login-ticker", "app-ticker"]) {
      const el = $(id);
      if (el) el.textContent = "";
      if (el) { const s1 = document.createElement("span"); s1.textContent = text; el.appendChild(s1); const s2 = document.createElement("span"); s2.textContent = text; el.appendChild(s2); }
    }
  }

  /* ---------------- HLS playback ---------------------------------------- */

  const canNativeHLS = document.createElement("video")
    .canPlayType("application/vnd.apple.mpegurl") !== "";

  function attachSource(video, src) {
    if (state.hls) { state.hls.destroy(); state.hls = null; }
    if (window.Hls && window.Hls.isSupported()) {
      const hls = new window.Hls({ enableWorker: true, maxBufferLength: 30 });
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(window.Hls.Events.ERROR, (_e, data) => {
        // Expired CloudFront cookies -> renew once, then retry the source.
        if (data.response && data.response.code === 403 && !video.dataset.retried) {
          video.dataset.retried = "1";
          api("/api/stream-auth").then(() => attachSource(video, src));
        }
      });
      state.hls = hls;
    } else if (canNativeHLS) {
      video.src = src; // Safari plays HLS natively
    }
  }

  function playVideo(v) {
    const video = $("player-video");
    delete video.dataset.retried;
    $("player-title").textContent = v.title.toUpperCase();
    $("player-folder").textContent = `THE ARCHIVE â€” ${v.folder.toUpperCase()}`;
    $("player-duration").textContent = fmtMin(v.duration_seconds);
    $("player-resolution").textContent =
      v.width && v.height ? `${v.width} Ã— ${v.height}` : "HLS";

    attachSource(video, v.playlist);

    const saved = Number(localStorage.getItem(`resume:${v.slug}`) || 0);
    if (saved > 5 && saved < v.duration_seconds - 5) {
      video.addEventListener("loadedmetadata", () => {
        video.currentTime = saved;
      }, { once: true });
    }
    video.ontimeupdate = () => {
      if (video.currentTime > 5) {
        localStorage.setItem(`resume:${v.slug}`, String(video.currentTime));
      }
    };
  }

  /* ---------------- hash router ----------------------------------------- */

  function route() {
    if (!routedOnce && state.videos.length === 0) return; // catalog not loaded yet
    routedOnce = true;
    const hash = location.hash || "#/";
    const find = (slug) => state.videos.find((v) => v.slug === slug);

    if (hash.startsWith("#/watch/")) {
      const v = find(hash.slice("#/watch/".length));
      if (v) { playVideo(v); show("player"); return; }
      // Unknown slug on a live session: home. On first load, keep the hash
      // so enterApp can retry after the catalog arrives.
      if (routedOnce && state.videos.length) { location.hash = "#/"; }
      return;
    }
    if (hash.startsWith("#/folder/")) {
      state.folder = decodeURIComponent(hash.slice("#/folder/".length));
      renderFolderNav(); renderGrid(); show("app");
      return;
    }
    if (hash.startsWith("#/upload")) {
      refreshStaged();
      show("upload");
      return;
    }
    state.folder = null;
    renderFolderNav(); renderFolders(); renderGrid(); show("app");
  }

  window.addEventListener("hashchange", route);
  // Reload any interrupted uploads on page load (resume after close/refresh).
  window.addEventListener("DOMContentLoaded", () => {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith("upload:")) {
        const [, name, size] = key.split(":");
        const sizeNum = Number(size);
        if (name && sizeNum > 0) {
          // Files cannot be re-read after a refresh; surface them as resumable
          // rows the user re-picks. Clear stale entries older than 7 days.
          const meta = JSON.parse(localStorage.getItem(key) || "null");
          if (!meta || !meta.uploadId) localStorage.removeItem(key);
        }
      }
    }
  });
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-nav]") && e.target.closest("[data-nav]").dataset.hash) {
      e.preventDefault();
      location.hash = e.target.closest("[data-nav]").dataset.hash;
    }
  });

  /* ---------------- uploads --------------------------------------------- */

  const uploadMeta = (file) => `upload:${file.name}:${file.size}`;

  function fmtBytes(n) {
    if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`;
    if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(0)} MB`;
    return `${(n / 1024).toFixed(0)} KB`;
  }

  function uploadCard(file) {
    const old = document.getElementById(`up-${CSS.escape(file.name)}-${file.size}`);
    if (old) old.remove();
    const card = document.createElement("div");
    card.className = "upload-card";
    card.id = `up-${CSS.escape(file.name)}-${file.size}`;
    card.innerHTML = `
      <div class="upload-card-head">
        <span class="video-title">${file.name}</span>
        <span class="mono-label">${fmtBytes(file.size)}</span>
      </div>
      <div class="upload-bar"><div></div></div>
      <div class="upload-card-foot">
        <span class="mono-label upload-status">STARTINGâ€¦</span>
        <span class="mono-label upload-speed"></span>
      </div>`;
    $("upload-queue").prepend(card);
    return card;
  }

  function setProgress(file, pct, status, speed) {
    const card = document.getElementById(`up-${CSS.escape(file.name)}-${file.size}`);
    if (!card) return;
    card.querySelector(".upload-bar > div").style.width = `${Math.max(2, pct)}%`;
    card.querySelector(".upload-status").textContent = status;
    card.querySelector(".upload-speed").textContent = speed || "";
  }

  const saveMeta = (file, meta) =>
    localStorage.setItem(uploadMeta(file), JSON.stringify(meta));
  const loadMeta = (file) => {
    try { return JSON.parse(localStorage.getItem(uploadMeta(file)) || "null"); }
    catch { return null; }
  };
  const clearMeta = (file) => localStorage.removeItem(uploadMeta(file));

  // Presigned part URLs come in pages of 100; cache the current page.
  const presignCache = new Map();
  async function presignUrl(meta, partNumber) {
    const pageIdx = Math.floor((partNumber - 1) / 100);
    const cacheKey = `${meta.uploadId}:${pageIdx}`;
    let page = presignCache.get(cacheKey);
    if (!page) {
      const r = await api(`/api/uploads/${meta.uploadId}`, {
        body: {
          key: meta.key,
          from_part: pageIdx * 100 + 1,
          to_part: pageIdx * 100 + 100,
        },
      });
      if (r.status !== 200) throw new Error(r.data.error || "presign failed");
      page = new Map(r.data.urls.map((u) => [u.part_number, u.url]));
      presignCache.set(cacheKey, page);
    }
    return page.get(partNumber);
  }

  const PART_CONCURRENCY = 3;
  const PART_RETRIES = 3;

  async function uploadFile(file, isRetry = false) {
    uploadCard(file);
    const startedAt = Date.now();

    // Resume: reuse the multipart session saved before a close/refresh.
    let meta = loadMeta(file);
    if (!meta) {
      const r = await api("/api/uploads", {
        body: { filename: file.name, size_bytes: file.size },
      });
      if (r.status !== 200) {
        setProgress(file, 0, (r.data.error || "REJECTED").toUpperCase(), "");
        return;
      }
      meta = { uploadId: r.data.upload_id, key: r.data.key,
               partSize: r.data.part_size, partCount: r.data.part_count };
      saveMeta(file, meta);
    }

    const completed = new Map(); // partNumber -> etag
    let uploadedBytes = 0;
    let nextPart = 1;
    let lastTick = Date.now(), lastBytes = 0;

    // Which parts are already on S3? (resume after close/refresh)
    let probeOk = false;
    try {
      const probe = await api(`/api/uploads/${meta.uploadId}`, {
        body: { key: meta.key, from_part: 1, to_part: 0 },
      });
      if (probe.status === 200) {
        probeOk = true;
        for (const p of probe.data.received || []) {
          completed.set(p.part_number, p.etag);
          uploadedBytes += p.size;
        }
        nextPart = Math.max(...completed.keys(), 0) + 1;
      }
    } catch { /* fresh upload */ }

    const tick = () => {
      const now = Date.now();
      if (now - lastTick < 700) return;
      const speed = ((uploadedBytes - lastBytes) * 1000) / (now - lastTick);
      const pct = (uploadedBytes / file.size) * 100;
      const eta = (file.size - uploadedBytes) / Math.max(speed, 1);
      setProgress(file, pct,
        `${completed.size}/${meta.partCount} PARTS`,
        `${fmtBytes(speed)}/S â€” ${eta > 90 ? `${Math.round(eta / 60)} MIN LEFT` : `${Math.round(eta)} S LEFT`}`);
      lastTick = now; lastBytes = uploadedBytes;
    };

    const total = meta.partCount;
    const worker = async () => {
      while (nextPart <= total) {
        const partNumber = nextPart++;
        if (completed.has(partNumber)) continue;
        const start = (partNumber - 1) * meta.partSize;
        const blob = file.slice(start, Math.min(start + meta.partSize, file.size));
        for (let attempt = 1; attempt <= PART_RETRIES; attempt++) {
          try {
            const url = await presignUrl(meta, partNumber);
            const res = await fetch(url, { method: "PUT", body: blob });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            completed.set(partNumber, res.headers.get("ETag").replaceAll('"', ""));
            uploadedBytes += blob.size;
            tick();
            break;
          } catch (err) {
            if (attempt === PART_RETRIES) throw err;
            await new Promise((r) => setTimeout(r, 1200 * attempt));
          }
        }
      }
    };

    try {
      if (!probeOk || completed.size < total) {
        await Promise.all(Array.from({ length: PART_CONCURRENCY }, worker));
      }
    } catch (err) {
      // A dead multipart session (expired/aborted on S3) is unrecoverable in
      // place â€” drop the stale session once and restart the file cleanly.
      if (!isRetry && /not found|expired|aborted|NoSuchUpload/i.test(err.message || "")) {
        await api("/api/uploads/abort", {
          body: { key: meta.key, upload_id: meta.uploadId },
        }).catch(() => {});
        clearMeta(file);
        presignCache.clear();
        return uploadFile(file, true);
      }
      setProgress(file, (uploadedBytes / file.size) * 100,
        "PAUSED â€” REOPEN TO RESUME", err.message);
      return; // meta stays in localStorage: next visit resumes
    }

    const done = await api("/api/uploads/complete", {
      body: { key: meta.key, upload_id: meta.uploadId,
              parts: [...completed].map(([part_number, etag]) =>
                     ({ part_number, etag })) },
    });
    if (done.status !== 200) {
      // All bytes are on S3; a later reopen probes the parts and re-completes.
      setProgress(file, 100, "FINISH FAILED â€” REOPEN TO RESUME",
        (done.data.error || "").toUpperCase());
      return;
    }
    const secs = (Date.now() - startedAt) / 1000;
    setProgress(file, 100, "UPLOADED â€” PACKAGINGâ€¦",
      `${fmtBytes(file.size / Math.max(secs, 1))}/S AVG`);
    clearMeta(file);
    refreshStaged();
    await waitForLive(file, done.data.slug);
  }

  // The Lambda packaging job runs for a minute or two after complete; poll
  // the catalog until the video shows up, then reload the archive live.
  async function waitForLive(file, slug) {
    if (!slug) {
      setProgress(file, 100, "LIVE SOON â€” REFRESH HOME PAGE", "");
      return;
    }
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 8000));
      const r = await api("/api/videos").catch(() => ({ status: 0 }));
      if (r.status === 200 &&
          (r.data.videos || []).some((v) => v.slug === slug)) {
        setProgress(file, 100, "LIVE âœ“ â€” ON THE ARCHIVE",
          "CLICK HOME TO WATCH");
        if (typeof enterApp === "function") enterApp(r.data);
        return;
      }
    }
    setProgress(file, 100, "STILL PACKAGING â€” CHECK HOME PAGE SOON",
      "OVERSIZED OR ODD CODEC? RUN publish.py");
  }

  /* ---------------- staged list ----------------------------------------- */

  async function refreshStaged() {
    const r = await api("/api/uploads");
    const list = $("staged-list");
    list.textContent = "";
    if (r.status !== 200) return;
    const uploads = r.data.uploads || [];
    for (const up of uploads) {
      const row = document.createElement("div");
      row.className = "staged-row";
      row.innerHTML = `
        <span class="video-title">${up.filename}</span>
        <span class="mono-label">${up.owner === state.username ? "YOURS â€” AWAITING publish.py" : up.owner.toUpperCase()}</span>
        <button class="btn btn-ghost btn-sm staged-abort">DISCARD</button>`;
      row.querySelector(".staged-abort").onclick = async () => {
        await api("/api/uploads/abort", {
          body: { key: up.key, upload_id: up.upload_id },
        });
        refreshStaged();
      };
      list.appendChild(row);
    }
    $("staged-section").hidden = uploads.length === 0;
  }


  function enterApp(data) {
    state.videos = data.videos || [];
    state.username = data.username;
    $("nav-user").textContent = `@${(data.username || "").toUpperCase()}`;
    $("hero-count").textContent = `${state.videos.length} TITLES`;
    $("hero-hours").textContent = totalHours(state.videos);
    renderTicker();
    route();
  }

  // Deep links like #/watch/<slug> must survive a fresh load: the router
  // needs the catalog, so park the hash and route once it arrives.
  let routedOnce = false;

  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("login-error");
    err.hidden = true;
    const btn = $("login-submit");
    btn.disabled = true;
    btn.textContent = "SIGNING INâ€¦";
    const { status, data } = await api("/api/login", {
      body: {
        username: $("login-username").value,
        password: $("login-password").value,
      },
    });
    btn.disabled = false;
    btn.textContent = "ENTER THE ARCHIVE";
    if (status !== 200) {
      err.textContent = data.error
        ? data.error.toUpperCase() : "SOMETHING WENT WRONG";
      err.hidden = false;
      return;
    }
    await api("/api/stream-auth"); // fresh CloudFront cookies alongside the session
    $("login-password").value = "";
    const { data: catalog } = await api("/api/videos");
    enterApp(catalog);
  });

  $("logout-btn").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST" });
    state.videos = [];
    state.username = null;
    if (state.hls) { state.hls.destroy(); state.hls = null; }
    location.hash = "";
    show("login");
  });

  $("back-btn").addEventListener("click", () => {
    const v = state.videos.find((x) => location.hash === `#/watch/${x.slug}`);
    location.hash = state.folder ? `#/folder/${encodeURIComponent(state.folder)}` : "#/";
    if (v) { const video = $("player-video"); video.pause(); }
  });

  $("nav-upload-btn").addEventListener("click", () => { location.hash = "#/upload"; });
  $("upload-back-btn").addEventListener("click", () => { location.hash = "#/"; });
  $("pick-btn").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", (e) => {
    for (const file of e.target.files) uploadFile(file);
    e.target.value = ""; // allow re-picking the same file
  });
  $("refresh-staged-btn").addEventListener("click", refreshStaged);

  // Drag a recording anywhere onto the upload page to start uploading it.
  const dropZone = document.querySelector(".upload-main");
  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("dragging");
  });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragging"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragging");
    for (const file of e.dataTransfer.files) uploadFile(file);
  });

  $("all-folders-btn").addEventListener("click", () => { location.hash = "#/"; });

  /* ---------------- boot -------------------------------------------------- */

  (async () => {
    const { status, data } = await api("/api/videos");
    if (status === 200) {
      enterApp(data); // cookie session still valid; routes the parked hash
    } else {
      show("login");
    }
  })();
})();
