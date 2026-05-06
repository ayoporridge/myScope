/**
 * Cloudflare Worker: Meilisearch 安全中转
 *
 * 功能：
 * - 用 Bearer 鉴权挡住公网直接访问 Meilisearch
 * - 只开放搜索接口（GET /search），不暴露管理 API
 * - 支持 CORS，让 Notion AI / Claude 可以跨域调用
 *
 * 部署步骤：见 README cloudflare-worker 章节
 */

// ── 环境变量（在 Cloudflare Dashboard 的 Workers → Settings → Variables 里配置）
// WORKER_SECRET   : 你自定义的 Bearer Token（与 .env 里的 CLOUDFLARE_WORKER_SECRET 一致）
// MEILI_HOST      : Mac mini 的公网地址或 DDNS，例如 http://your-ddns.com:7700
// MEILI_MASTER_KEY: Meilisearch 的 Master Key

const INDEX_NAME = "hubble";

export default {
  async fetch(request, env) {
    // ── CORS 预检
    if (request.method === "OPTIONS") {
      return corsResponse(new Response(null, { status: 204 }));
    }

    // ── 鉴权
    const auth = request.headers.get("Authorization") || "";
    if (auth !== `Bearer ${env.WORKER_SECRET}`) {
      return corsResponse(new Response(JSON.stringify({ error: "Unauthorized" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }));
    }

    const url = new URL(request.url);
    const path = url.pathname;

    // ── 只允许搜索接口
    if (!path.startsWith("/search")) {
      return corsResponse(new Response(JSON.stringify({ error: "Not allowed" }), {
        status: 403,
        headers: { "Content-Type": "application/json" },
      }));
    }

    // ── 解析搜索参数
    const q      = url.searchParams.get("q") || "";
    const limit  = Math.min(parseInt(url.searchParams.get("limit") || "10"), 30);
    const filter = url.searchParams.get("filter") || "";

    if (!q.trim()) {
      return corsResponse(new Response(JSON.stringify({ error: "q is required" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }));
    }

    // ── 转发到 Meilisearch
    const meiliUrl = `${env.MEILI_HOST}/indexes/${INDEX_NAME}/search`;
    const body = {
      q,
      limit,
      attributesToRetrieve: ["title", "content", "url", "feed_title", "published_date"],
      attributesToHighlight: ["title", "content"],
      highlightPreTag: "**",
      highlightPostTag: "**",
      ...(filter ? { filter } : {}),
    };

    const meiliResp = await fetch(meiliUrl, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.MEILI_MASTER_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });

    const data = await meiliResp.json();

    // ── 只返回必要字段，减少 token 消耗
    const hits = (data.hits || []).map(h => ({
      title:        h.title,
      content:      h.content?.slice(0, 300),
      url:          h.url,
      source:       h.feed_title,
      published:    h.published_date,
    }));

    return corsResponse(new Response(JSON.stringify({ hits, total: data.estimatedTotalHits }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  }
};

function corsResponse(response) {
  const headers = new Headers(response.headers);
  headers.set("Access-Control-Allow-Origin", "*");
  headers.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  headers.set("Access-Control-Allow-Headers", "Authorization, Content-Type");
  return new Response(response.body, { status: response.status, headers });
}
