// Netlify function — auto-triggered when ANY form is submitted to Netlify.
// We only care about the hero "Get a demo call" form (form-name=demo-call):
// Netlify has already stored + spam-filtered the submission (that's the consent
// audit trail); this function bridges to the VPS, which holds the Twilio
// credentials and fires the outbound AI callback. Twilio creds never live here.
//
// The filename matters: Netlify only auto-runs a function on form submissions if
// it is named submission-created.js.

const { DEMO_BACKEND_URL = "", DEMO_CALL_SECRET = "" } = process.env;

exports.handler = async function (event) {
  let payload = {};
  try {
    payload = JSON.parse(event.body || "{}").payload || {};
  } catch {
    payload = {};
  }

  // Ignore every form that isn't the demo-call form.
  const formName = payload.title || payload.form_name;
  if (formName !== "demo-call") {
    return { statusCode: 204 };
  }

  const data = payload.data || {};
  const name = String(data.name || "").trim();
  const phone = String(data.phone || "").trim();
  const consentRaw = String(data.consent || "").toLowerCase();
  const consent = consentRaw === "true" || consentRaw === "on" || consentRaw === "yes";

  // Belt-and-braces honeypot check (Netlify filters these too).
  if (data["bot-field"]) {
    return { statusCode: 204 };
  }
  if (!name || !phone || !consent) {
    console.warn("[demo-call] incomplete submission, skipping", {
      hasName: !!name,
      hasPhone: !!phone,
      consent,
    });
    return { statusCode: 204 };
  }

  if (!DEMO_BACKEND_URL || !DEMO_CALL_SECRET) {
    console.error("[demo-call] DEMO_BACKEND_URL / DEMO_CALL_SECRET not set on Netlify");
    return { statusCode: 500 };
  }

  try {
    const res = await fetch(
      `${DEMO_BACKEND_URL.replace(/\/$/, "")}/api/demo-call`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Demo-Secret": DEMO_CALL_SECRET,
        },
        body: JSON.stringify({ name, phone, consent: true }),
      }
    );
    if (!res.ok) {
      // 429 (rate limit) etc. — log but don't alarm the submitter.
      console.warn("[demo-call] backend rejected:", res.status, await res.text());
    } else {
      console.log("[demo-call] callback fired for", name);
    }
    return { statusCode: 200 };
  } catch (err) {
    console.error("[demo-call] backend call failed:", err);
    return { statusCode: 200 };
  }
};
