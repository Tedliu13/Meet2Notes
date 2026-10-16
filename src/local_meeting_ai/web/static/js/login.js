(() => {
  "use strict";
  let registering = false;
  const form = document.querySelector("#account-form");
  const submit = document.querySelector("#account-submit");
  const error = document.querySelector("#account-error");
  const password = document.querySelector("#account-password");
  const name = document.querySelector("#account-name");
  function mode(register) {
    registering = register;
    document.querySelector("#name-field").hidden = !register;
    document.querySelector("#register-help").hidden = !register;
    name.required = register;
    password.minLength = register ? 12 : 1;
    password.autocomplete = register ? "new-password" : "current-password";
    document.querySelector("#choose-login").setAttribute("aria-pressed", String(!register));
    document.querySelector("#choose-register").setAttribute("aria-pressed", String(register));
    submit.textContent = register ? "註冊並登入" : "登入";
    error.textContent = "";
  }
  document.querySelector("#choose-login").addEventListener("click", () => mode(false));
  document.querySelector("#choose-register").addEventListener("click", () => mode(true));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    submit.disabled = true;
    document.querySelectorAll(".account-tabs button").forEach((button) => { button.disabled = true; });
    error.textContent = "";
    try {
      const payload = { username: document.querySelector("#account-username").value.trim(), password: password.value };
      if (registering) payload.name = name.value.trim();
      const response = await fetch(`/api/auth/${registering ? "register" : "login"}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "請檢查帳號、姓名與密碼格式");
      password.value = "";
      location.assign("/");
    } catch (failure) {
      error.textContent = failure.message || "暫時無法登入，請重試";
    } finally {
      submit.disabled = false;
      document.querySelectorAll(".account-tabs button").forEach((button) => { button.disabled = false; });
    }
  });
})();
