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
        if (!url) return showError("Please paste an Instagram link.");

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
        title.textContent = data.title || "Instagram media";
        thumbnail.src = data.thumbnail || "";
        thumbnail.alt = data.title ? `Thumbnail for ${data.title}` : "Media thumbnail";
        thumbnail.hidden = !data.thumbnail;
        formats.replaceChildren();
        (data.formats || []).forEach((format) => {
            const button = document.createElement("button");
            button.className = "format-btn";
            button.type = "button";
            button.textContent = format.quality || format.format_id;
            const ext = document.createElement("small");
            ext.textContent = format.ext || "file";
            button.append(ext);
            button.addEventListener("click", () => downloadFormat(format.format_id, button));
            formats.append(button);
        });
        if (!formats.children.length) {
            formats.textContent = "No downloadable formats were found.";
        }
        result.classList.remove("hidden");
        result.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    async function downloadFormat(formatId, button) {
        clearError();
        button.disabled = true;
        try {
            const response = await fetch("/api/download", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url: currentUrl, format_id: formatId, page_key: pageKey })
            });
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.error || "Download failed.");
            }
            const objectUrl = URL.createObjectURL(await response.blob());
            const link = document.createElement("a");
            link.href = objectUrl;
            link.download = "instagram-media";
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
