import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { useAuth } from "../lib/auth";

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(email, password);
    } catch (err) {
      setError(err.response?.data?.detail || "Gagal masuk");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login-page">
      <div className="login-mark">
        <span>EP</span>
        <p>ETERNAL PACKAGING</p>
      </div>
      <section className="login-panel">
        <p className="eyebrow">SISTEM PRODUKSI / 01</p>
        <h1>
          Jaga setiap<br />
          <em>cetakan</em> tetap jalan.
        </h1>
        <p className="login-copy">Satu alur jelas dari Order Masuk sampai Terkirim.</p>
        <form onSubmit={submit} data-testid="login-form">
          <label>
            Email
            <input data-testid="login-email-input" value={email} onChange={(e) => setEmail(e.target.value)} type="email" required />
          </label>
          <label>
            Kata Sandi
            <input data-testid="login-password-input" value={password} onChange={(e) => setPassword(e.target.value)} type="password" required />
          </label>
          {error && <div className="error" data-testid="login-error">{error}</div>}
          <button className="primary full" data-testid="login-submit-button" disabled={busy}>
            {busy ? "Memproses..." : "Masuk ke workspace"} <ChevronRight size={18} />
          </button>
        </form>
        <p className="demo-note" data-testid="login-note">Gunakan akun operasional Anda. Hubungi Admin bila password terlupa.</p>
      </section>
    </main>
  );
}
