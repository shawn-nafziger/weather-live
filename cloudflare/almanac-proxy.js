/*
 * almanac-proxy: serves the Weather Almanac at tallorderdigital.com/weather/almanac/ (added Nov 10, 2026).
 *
 * The Almanac is built and published every day by this repo's daily workflow to GitHub Pages
 * (https://shawn-nafziger.github.io/weather-live/almanac/). This Worker answers requests for
 * tallorderdigital.com/weather/almanac/... by fetching the same path from there, so readers and search
 * engines see the Almanac as a folder of the main site: links and mentions build one domain.
 * Everything else on tallorderdigital.com is untouched (the route only covers /weather/almanac*).
 *
 * Lives in Shawn's Cloudflare account as the Worker "almanac-proxy". This file is the copy of record:
 * edit it here by PR, then paste it into the dashboard (Workers & Pages > almanac-proxy > Edit code).
 *
 * Set up in the Cloudflare dashboard (one time):
 *   - Route: tallorderdigital.com/weather/almanac*  (zone tallorderdigital.com) -> this Worker
 *   - The tallorderdigital.com DNS records must be Proxied (orange cloud) for the route to run,
 *     with SSL/TLS mode "Full" (never "Flexible", which loops with GitHub's HTTPS redirect).
 *   - The old address weather.tallorderdigital.com redirects here with two Redirect Rules (see README).
 */
const ORIGIN = "https://shawn-nafziger.github.io/weather-live/almanac";
const PREFIX = "/weather/almanac";

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
    }
    // "/weather/almanac" without the slash: send it to the folder, so the page's relative links work.
    if (url.pathname === PREFIX) return Response.redirect(url.origin + PREFIX + "/" + url.search, 301);
    if (!url.pathname.startsWith(PREFIX + "/")) return fetch(request); // not ours (e.g. /weather/almanac-something)

    const rest = url.pathname.slice(PREFIX.length); // "/", "/chicago/", "/chicago/data.json", "/sitemap.xml"
    const upstream = await fetch(ORIGIN + rest + url.search, {
      method: request.method,
      redirect: "manual",
      cf: { cacheEverything: true, cacheTtl: 600 }, // GitHub Pages' own cache time; pages change once a day
    });

    // GitHub Pages redirects a folder without its slash ("/chicago" -> "/chicago/"); point that back at our address.
    if (upstream.status >= 300 && upstream.status < 400) {
      const loc = new URL(upstream.headers.get("Location") || "/", ORIGIN + "/");
      const theirs = new URL(ORIGIN + "/");
      const path = loc.pathname.startsWith(theirs.pathname) ? loc.pathname.slice(theirs.pathname.length - 1) : "/";
      return Response.redirect(url.origin + PREFIX + path + loc.search, upstream.status === 301 ? 301 : 302);
    }

    const headers = new Headers(upstream.headers);
    for (const h of ["x-github-request-id", "x-fastly-request-id", "x-served-by", "x-cache", "x-cache-hits", "x-timer", "via"]) headers.delete(h);
    return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers });
  },
};
