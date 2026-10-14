// Fetch credentials for one explicitly selected lock. Do not publish the output.
import pkg from "eufy-security-client";
import { writeFileSync } from "node:fs";
import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";

const { HTTPApi, PublicKeyType } = pkg;
const email = process.env.EUFY_EMAIL;
const password = process.env.EUFY_PASSWORD;
const country = process.env.EUFY_COUNTRY?.toUpperCase();
const serial = process.env.EUFY_LOCK_SN;
if (!email || !password || !/^[A-Z]{2}$/.test(country || "") || !serial) {
  console.error("Set EUFY_EMAIL, EUFY_PASSWORD, EUFY_COUNTRY and EUFY_LOCK_SN.");
  process.exit(1);
}
const rl = readline.createInterface({ input, output });
const api = await HTTPApi.initialize(country, email, password);
api.setPhoneModel?.("Pixel 7");
api.on("connection error", () => console.error("Connection error. Check account and server country."));
api.on("captcha request", async (id, dataUrl) => {
  const base64 = String(dataUrl).replace(/^data:image\/\w+;base64,/, "");
  writeFileSync("captcha.png", Buffer.from(base64, "base64"));
  console.log("Open captcha.png to read the image.");
  const code = await rl.question("Captcha: ");
  await api.login({ captcha: { captchaId: id, captchaCode: code.trim() } });
});
api.on("tfa request", async () => {
  const code = await rl.question("Verification code sent by eufy: ");
  await api.login({ verifyCode: code.trim() });
});
try {
  await api.login();
  for (let i = 0; i < 120 && !api.isConnected?.(); i++) {
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  if (!api.isConnected?.()) throw new Error("Login did not complete.");
  const stations = await api.getStationList();
  const station = stations.find((item) => item.station_sn === serial);
  if (!station?.member?.admin_user_id) throw new Error("Selected lock not found.");
  const key = await api.getPublicKey(serial, PublicKeyType?.LOCK ?? 2);
  if (!key) throw new Error("Public key unavailable.");
  const member = station.member;
  console.log("Selected-lock credentials for manual setup; keep this output private:");
  console.log(JSON.stringify({
    serial,
    admin_user_id: member.admin_user_id,
    short_user_id: member.short_user_id ?? 0,
    lock_public_key: key,
    nickname: member.nick_name || `${email.split("@")[0].slice(0, 3)}***`,
  }, null, 2));
  rl.close();
  process.exit(0);
} catch {
  console.error("Could not fetch the selected lock. Check credentials, country and serial locally.");
  rl.close();
  process.exit(1);
}
