// weather-live-trigger: starts the weather-live GitHub jobs on time.
// GitHub's own schedule skips runs, so Cloudflare asks GitHub to run them.
// Schedules (Settings > Trigger events, UTC):
//   "7 * * * *"        -> hourly weather pull
//   "37 11,15 * * *"   -> daily official records
const REPO = "shawn-nafziger/weather-live";
const DAILY_CRON = "37 11,15 * * *";

export default {
  async scheduled(event, env, ctx) {
    const workflow = event.cron === DAILY_CRON ? "daily.yml" : "hourly.yml";
    ctx.waitUntil(startWorkflow(env, workflow));
  },
  // Visiting the address does nothing except confirm the Worker is alive.
  async fetch() {
    return new Response("weather-live trigger");
  },
};

async function startWorkflow(env, workflow) {
  const r = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "weather-live-trigger",
      },
      body: JSON.stringify({ ref: "main" }),
    },
  );
  // GitHub answers 204 when the run is started.
  if (r.status === 204) console.log(`Started ${workflow}`);
  else console.error(`Could not start ${workflow}: HTTP ${r.status} ${await r.text()}`);
}
