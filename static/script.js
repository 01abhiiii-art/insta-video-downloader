document.addEventListener("DOMContentLoaded", () => {
    const form = document.querySelector("#input-section");
    const input = document.querySelector("#video-url");
    const pasteButton = document.querySelector("#paste-btn");
    const downloadButton = document.querySelector("#download-btn");
    const loading = document.querySelector("#loading");
    const error = document.querySelector("#error-message");
    const result = document.querySelector("#result-content");
    const backButton = document.querySelector("#back-btn");
    const title = document.querySelector("#video-title");
    const thumbnail = document.querySelector("#thumbnail");
    const formats = document.querySelector("#formats-container");
    const themeToggle = document.querySelector("#theme-toggle");
    const pageKey = form.dataset.pageKey || "video";
    let currentUrl = "";

    pasteButton.addEventListener("click", async () => {
        try {
            input.value = await navigator.clipboard.readText();
            input.focus();
        } catch {
            input.focus();
        }
    });

    themeToggle.addEventListener("click", () => {
        document.body.classList.toggle("night");
        themeToggle.textContent = document.body.classList.contains("night") ? "☀" : "☾";
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const url = input.value.trim();
        if (!url) return showError("Please paste a YouTube link.");

        currentUrl = url;
        clearError();
        loading.classList.remove("hidden");
        result.classList.add("hidden");
        downloadButton.disabled = true;

        try {
            const response = await fetch("/api/info", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url, page_key: pageKey })
            });
            const data = await readJson(response);
            renderResult(data);
        } catch (requestError) {
            showError(requestError.message);
        } finally {
            loading.classList.add("hidden");
            downloadButton.disabled = false;
        }
    });

    backButton.addEventListener("click", () => {
        result.classList.add("hidden");
        input.focus();
        clearError();
        window.scrollTo({ top: 0, behavior: "smooth" });
    });

    async function readJson(response) {
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "Something went wrong.");
        return data;
    }

    function renderResult(data) {
        title.textContent = data.title || "YouTube media";
        thumbnail.src = data.thumbnail || "";
        thumbnail.alt = data.title ? `Thumbnail for ${data.title}` : "Video thumbnail";
        thumbnail.hidden = !data.thumbnail;
        formats.replaceChildren();
        ["video", "audio"].forEach((kind) => {
            const available = (data.formats || []).filter((format) => format.kind === kind);
            if (!available.length) return;
            const section = document.createElement("section");
            section.className = "format-group";
            const heading = document.createElement("h3");
            heading.textContent = kind === "audio" ? "Audio downloads" : "Video downloads";
            section.append(heading);
            const grid = document.createElement("div");
            grid.className = "formats";
            available.forEach((format) => {
                const button = document.createElement("button");
                button.className = "format-btn";
                button.type = "button";
                button.textContent = kind === "audio"
                    ? `MP3 · ${format.quality || "Audio"}`
                    : `${format.quality || format.format_id} MP4`;
                const ext = document.createElement("small");
                const size = format.size ? ` · ${format.size}` : "";
                ext.textContent = kind === "audio"
                    ? `192 kbps conversion${size}`
                    : `${format.ext || "file"} · audio merged${size}`;
                button.append(ext);
                button.addEventListener("click", () => downloadFormat(format.format_id, kind, button));
                grid.append(button);
            });
            section.append(grid);
            formats.append(section);
        });
        if (!formats.children.length) {
            formats.textContent = "No downloadable formats were found.";
        }
        result.classList.remove("hidden");
        result.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    async function downloadFormat(formatId, kind, button) {
        clearError();
        const originalLabel = button.innerHTML;
        button.disabled = true;
        button.textContent = "Preparing download…";
        try {
            const response = await fetch("/api/download", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url: currentUrl, format_id: formatId, kind, page_key: pageKey })
            });
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.error || "Download failed.");
            }
            const objectUrl = URL.createObjectURL(await response.blob());
            const link = document.createElement("a");
            link.href = objectUrl;
            link.download = kind === "audio" ? "clipfetch-audio.mp3" : "clipfetch-video.mp4";
            link.style.display = "none";
            document.body.append(link);
            link.click();
            setTimeout(() => {
                URL.revokeObjectURL(objectUrl);
                link.remove();
            }, 1000);
        } catch (requestError) {
            showError(requestError.message);
        } finally {
            button.innerHTML = originalLabel;
            button.disabled = false;
        }
    }

    function showError(message) {
        error.textContent = message;
        error.classList.remove("hidden");
    }

    function clearError() {
        error.textContent = "";
        error.classList.add("hidden");
    }
});
