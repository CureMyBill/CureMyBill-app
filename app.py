"""
CureMyBill — Analyse de factures médicales américaines avec Claude Vision.

Un utilisateur upload une facture médicale (PDF ou image), l'app :
1. Extrait les codes CPT, descriptions et montants via Claude Vision
2. Compare les prix à un barème de référence
3. Génère une lettre de contestation en anglais si des écarts sont détectés
"""

import base64
import io
import json
import os
import re
from xml.sax.saxutils import escape as xml_escape

import audit_store
import pandas as pd
import streamlit as st
from anthropic import Anthropic
from reportlab.lib.pagesizes import letter as LETTER_PAGESIZE
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
import streamlit.components.v1 as components
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

MODEL = "claude-sonnet-5"
FEE_SCHEDULE_PATH = os.path.join(os.path.dirname(__file__), "fee_schedule.csv")

# --- Paddle (Sandbox) ---
# ⚠️ Remplace PADDLE_CLIENT_TOKEN par ton "Client-side token" Sandbox
# (Paddle > Developer Tools > Authentication). C'est une clé PUBLIQUE,
# sans risque à laisser dans le code (contrairement à une clé API secrète).
PADDLE_CLIENT_TOKEN = "test_4a84f6454a686c1c88f85474e51"
PADDLE_ENVIRONMENT = "sandbox"  # passe à "production" quand ton compte est validé en Live
PADDLE_PRICE_STANDARD = "pri_01m1j5vfzeks8kdyw748xxf7eb"  # Standard — $19
PADDLE_PRICE_PRO = "pri_01m1j61bcz45m327s3a1bhnnn5"  # Pro — $34
# ⚠️ Crée ces deux produits dans Paddle (Catalog > Products) puis colle leurs
# vrais Price IDs ici. Tant que ce sont des placeholders, les cases à cocher
# des add-ons dans le checkout ne fonctionneront pas.
PADDLE_PRICE_INSURANCE_APPEAL = "PASTE_YOUR_INSURANCE_APPEAL_PRICE_ID_HERE"  # +$14
PADDLE_PRICE_PHONE_SCRIPT = "PASTE_YOUR_PHONE_SCRIPT_PRICE_ID_HERE"  # +$9

st.set_page_config(
    page_title="CureMyBill",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# --------------------------------------------------------------------------
# Style — interface moderne et épurée
# --------------------------------------------------------------------------

CUSTOM_CSS = """
<style>
:root {
    --accent: #2563eb;
    --accent-light: #eff6ff;
    --danger: #dc2626;
    --danger-light: #fef2f2;
    --success: #059669;
    --success-light: #ecfdf5;
    --text-main: #111827;
    --text-muted: #6b7280;
    --border: #e5e7eb;
}

.stApp {
    background: #fafafa;
}

h1, h2, h3 { color: var(--text-main); font-weight: 700; }

.mb-hero {
    padding: 1.75rem 2rem;
    border-radius: 16px;
    background: linear-gradient(135deg, #2563eb 0%, #1e40af 100%);
    color: white;
    margin-bottom: 1.5rem;
}
.mb-hero h1 { color: white; margin: 0; font-size: 1.6rem; }
.mb-hero p { color: #dbeafe; margin: 0.4rem 0 0 0; font-size: 0.95rem; }

.mb-card {
    background: white;
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 1.4rem 1.6rem;
    margin-bottom: 1.2rem;
    transition: box-shadow 0.25s ease, transform 0.25s ease;
}
.mb-card:hover {
    box-shadow: 0 8px 24px rgba(17, 24, 39, 0.08);
    transform: translateY(-2px);
}

.mb-badge {
    display: inline-block;
    padding: 0.15rem 0.6rem;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
}
.mb-badge-danger { background: var(--danger-light); color: var(--danger); }
.mb-badge-success { background: var(--success-light); color: var(--success); }
.mb-badge-neutral { background: #f3f4f6; color: var(--text-muted); }

.mb-stat {
    text-align: center;
    padding: 1rem;
    border-radius: 12px;
    background: var(--accent-light);
    transition: transform 0.2s ease;
}
.mb-stat:hover { transform: scale(1.03); }
.mb-stat .value { font-size: 1.6rem; font-weight: 700; color: var(--accent); }
.mb-stat .label { font-size: 0.8rem; color: var(--text-muted); margin-top: 0.2rem; }

.mb-stat.danger { background: var(--danger-light); }
.mb-stat.danger .value { color: var(--danger); }

/* Barre d'étapes du parcours */
.mb-steps {
    display: flex;
    justify-content: space-between;
    margin-bottom: 1.5rem;
    gap: 8px;
}
.mb-step {
    flex: 1;
    text-align: center;
    padding: 0.6rem 0.4rem;
    border-radius: 10px;
    background: #f3f4f6;
    color: var(--text-muted);
    font-size: 0.78rem;
    font-weight: 600;
    transition: all 0.3s ease;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
}
.mb-step.done {
    background: var(--success-light);
    color: var(--success);
}
.mb-step.active {
    background: var(--accent);
    color: white;
    transform: scale(1.05);
    box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);
}

section[data-testid="stSidebar"] { display: none; }

.mb-topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.6rem 0;
    margin-bottom: 0.5rem;
}
.mb-brand {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 800;
    font-size: 1.15rem;
    color: var(--text-main);
}

.mb-icon-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
}

.stButton > button {
    border-radius: 10px;
    font-weight: 600;
    padding: 0.55rem 1.4rem;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.stButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 10px rgba(0,0,0,0.12);
}

/* Bouton principal (type="primary") : gros, visible, impossible à rater */
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
    border: none;
    color: white;
    font-size: 1.05rem;
    font-weight: 700;
    padding: 0.75rem 2rem;
    border-radius: 12px;
    box-shadow: 0 4px 14px rgba(37, 99, 235, 0.35);
}
.stButton > button[kind="primary"]:hover {
    box-shadow: 0 6px 20px rgba(37, 99, 235, 0.45);
    transform: translateY(-2px);
}
.stButton > button[kind="primary"]:disabled {
    background: #d1d5db;
    color: #6b7280;
    box-shadow: none;
}

/* Contrainte de largeur + centrage, pour éviter que tout s'éparpille sur grand écran */
.block-container {
    max-width: 1100px;
    padding-top: 1.5rem;
    padding-bottom: 2rem;
    margin: 0 auto;
}

/* Resserre l'espace vertical par défaut entre les blocs Streamlit */
div[data-testid="stVerticalBlock"] > div {
    gap: 0.5rem;
}

/* Champs de formulaire : libellés bien lisibles */
.stTextInput label, .stSelectbox label, .stTextArea label {
    color: #111827 !important;
    font-weight: 600 !important;
    font-size: 0.85rem !important;
}
.stTextInput input, .stTextArea textarea {
    border-radius: 8px !important;
}

/* Floute le tableau d'audit détaillé (CPT/montants) jusqu'au paiement —
   on ne peut pas envelopper st.dataframe dans un div classique, donc on
   cible l'élément qui suit immédiatement notre marqueur invisible. */
div[data-testid="element-container"]:has(.mb-blur-marker) + div[data-testid="element-container"] {
    filter: blur(6px);
    pointer-events: none;
    user-select: none;
}

/* Bouton "Generate the letter" — CTA distinct, impossible à manquer */
div[data-testid="element-container"]:has(.mb-cta-marker) + div[data-testid="element-container"] .stButton > button {
    background: linear-gradient(135deg, #ec4899 0%, #db2777 100%);
    border: none;
    color: white;
    font-size: 1.1rem;
    font-weight: 800;
    padding: 0.9rem 2.2rem;
    border-radius: 14px;
    box-shadow: 0 6px 20px rgba(219, 39, 119, 0.45);
    animation: mb-pulse 2s ease-in-out infinite;
}
div[data-testid="element-container"]:has(.mb-cta-marker) + div[data-testid="element-container"] .stButton > button:hover {
    box-shadow: 0 8px 26px rgba(219, 39, 119, 0.55);
    transform: translateY(-2px) scale(1.02);
}
@keyframes mb-pulse {
    0%, 100% { box-shadow: 0 6px 20px rgba(219, 39, 119, 0.45); }
    50% { box-shadow: 0 6px 28px rgba(219, 39, 119, 0.7); }
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# --------------------------------------------------------------------------
# Entry gate — language + email + legal disclaimer (must pass before anything else)
# --------------------------------------------------------------------------

_GATE_TEXT = {
    "en": {
        "title": "Welcome to CureMyBill",
        "subtitle": "Understand your medical bill and challenge unfair charges.",
        "lang_label": "Choose your language",
        "email_label": "Email address",
        "email_placeholder": "you@example.com",
        "disclaimer": (
            "**CureMyBill is an automated document-assistance tool.** It does not "
            "provide medical or legal advice and is not a substitute for an "
            "attorney, a licensed medical billing advocate, or your insurance "
            "carrier. By continuing, you consent to the automated processing of "
            "the personal and medical billing information contained in the "
            "document you upload."
        ),
        "checkbox": "I have read and accept this notice, and I consent to the automated processing of my document.",
        "continue": "Continue",
        "email_error": "Please enter a valid email address.",
        "checkbox_error": "You must accept the notice above to continue.",
    },
    "es": {
        "title": "Bienvenido a CureMyBill",
        "subtitle": "Entienda su factura médica y conteste los cargos injustos.",
        "lang_label": "Elija su idioma",
        "email_label": "Correo electrónico",
        "email_placeholder": "usted@ejemplo.com",
        "disclaimer": (
            "**CureMyBill es una herramienta automatizada de asistencia "
            "documental.** No brinda asesoría médica ni legal y no sustituye a "
            "un abogado, un defensor de facturación médica con licencia, ni a "
            "su compañía de seguros. Al continuar, usted acepta el "
            "procesamiento automatizado de la información personal y de "
            "facturación médica contenida en el documento que suba."
        ),
        "checkbox": "He leído y acepto este aviso, y consiento el procesamiento automatizado de mi documento.",
        "continue": "Continuar",
        "email_error": "Por favor ingrese un correo electrónico válido.",
        "checkbox_error": "Debe aceptar el aviso anterior para continuar.",
    },
}


def render_entry_gate() -> None:
    if "app_lang" not in st.session_state:
        st.session_state.app_lang = "en"

    _, gate_col, _ = st.columns([1, 2, 1])
    with gate_col:
        st.write("")
        st.write("")
        lang_choice = st.radio(
            "Language / Idioma",
            options=["en", "es"],
            format_func=lambda v: "English (US)" if v == "en" else "Español",
            horizontal=True,
            index=0 if st.session_state.app_lang == "en" else 1,
            label_visibility="collapsed",
        )
        st.session_state.app_lang = lang_choice
        t = _GATE_TEXT[lang_choice]

        st.markdown(f"## 🩺 {t['title']}")
        st.markdown(f"<p style='color:#6b7280;'>{t['subtitle']}</p>", unsafe_allow_html=True)

        with st.container(border=True):
            email = st.text_input(t["email_label"], placeholder=t["email_placeholder"])
            st.info(t["disclaimer"])
            accepted = st.checkbox(t["checkbox"])

            if st.button(t["continue"], type="primary", use_container_width=True):
                email_valid = bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""))
                if not email_valid:
                    st.error(t["email_error"])
                elif not accepted:
                    st.error(t["checkbox_error"])
                else:
                    st.session_state.legal_accepted = True
                    st.session_state.user_email = email
                    st.rerun()


if "legal_accepted" not in st.session_state:
    st.session_state.legal_accepted = False

if not st.session_state.legal_accepted:
    render_entry_gate()
    st.stop()

# --------------------------------------------------------------------------
# UI translations (rest of the app, once past the entry gate)
# --------------------------------------------------------------------------

UI_TEXT = {
    "en": {
        "sidebar_api_label": "API Key",
        "sidebar_api_placeholder": "sk-ant-...",
        "sidebar_api_help": "Your key is never stored — it only stays in memory for this session.",
        "api_key_from_env": "✅ API key loaded from the ANTHROPIC_API_KEY environment variable.",
        "settings_panel_label": "⚙️ Settings",
        "sidebar_how_title": "**How it works**",
        "sidebar_how_steps": (
            "1. Upload a bill (PDF/image)\n"
            "2. We extract the CPT codes and amounts\n"
            "3. Comparison against a reference fee schedule\n"
            "4. Generation of a dispute letter\n"
        ),
        "sidebar_caption": (
            "ℹ️ The included fee schedule uses official national Medicare "
            "rates (a public, citable benchmark) — not a subjective 'fair "
            "price'. Medicare rates are usually well below what a hospital "
            "bills — that's normal, and it's exactly what makes the "
            "argument strong in a dispute letter."
        ),
        "hero_title": "🩺 CureMyBill",
        "hero_subtitle": (
            "Analyze your US medical bill, spot overcharges, and generate "
            "a dispute letter in seconds."
        ),
        "upload_label": "Drop your medical bill (PDF, PNG, or JPG)",
        "analyze_button": "🔍 Analyze the bill",
        "err_need_api_key": "Please enter your Anthropic API key in the sidebar.",
        "spinner_reading": "Reading the bill...",
        "err_bad_json": (
            "We couldn't parse a usable result. Try again, or check that "
            "the file is readable (not too blurry or skewed)."
        ),
        "err_analysis": "Error during analysis: {e}",
        "section_bill_info_title": "📄 Bill information — review and complete if needed",
        "caption_bill_info": (
            "This was extracted automatically. Correct anything that's "
            "wrong — it will be injected as-is into the final letter."
        ),
        "label_patient_name": "Patient name",
        "label_provider_name": "Hospital / provider name",
        "label_provider_address": "Hospital address",
        "label_account_number": "Account / claim number",
        "label_bill_date": "Bill date",
        "label_sender_title": "**Your contact info (letter sender)**",
        "label_sender_name": "Your full name",
        "label_sender_address": "Your address (street, number)",
        "label_sender_city_state_zip": "City, State, ZIP",
        "label_letter_date": "Letter date",
        "section_comparison_title": "💰 Comparison to the Medicare rate",
        "stat_total_billed": "Total billed",
        "stat_outlier_amount": "Gap on the most suspicious lines",
        "stat_outlier_count": "Lines far above the norm",
        "table_lock_label": "Itemized audit locked — unlock below to see exact codes & amounts",
        "caption_comparison_methodology": (
            "Comparison against the official national Medicare rate (a "
            "public, citable benchmark). A hospital normally bills several "
            "times this rate — 'Typical range' covers a common markup (up "
            "to ~5x Medicare). Beyond that, the bigger the gap, the "
            "stronger the case for a dispute."
        ),
        "section_letter_title": "✉️ Dispute letter",
        "info_no_elevated_lines": (
            "No line is dramatically out of range, but some are above the "
            "usual markup — check the table above."
        ),
        "info_no_disputable_lines": "No significantly out-of-range line was detected.",
        "button_generate_letter": "✍️ Generate the letter",
        "spinner_writing_letter": "Writing the letter...",
        "err_letter_generation": "Error generating the letter: {e}",
        "lock_label": "🔒 Full letter locked",
        "unlock_cta": "**Unlock your letter to download the PDF:**",
        "caption_payment_security": (
            "Secure payment via Paddle (Sandbox mode — no real card is "
            "charged until the account goes Live). The download starts "
            "automatically in your browser right after payment, without "
            "reloading the page."
        ),
        "success_unlocked": "✅ Access unlocked — {plan} plan.",
        "label_letter_generated": "Generated letter",
        "download_button_pdf": "⬇️ Download the letter as PDF",
        "err_pdf_generation": "Error generating the PDF: {e}",
        "download_button_txt_fallback": "⬇️ Download the letter (.txt) — fallback",
        "pro_section_title": "**📬 Pro option — Follow-up letter (if no response within 30 days)**",
        "button_generate_followup": "✍️ Generate the follow-up letter",
        "err_followup_generation": "Error generating the follow-up: {e}",
        "label_followup_generated": "Generated follow-up letter",
        "download_button_followup_pdf": "⬇️ Download the follow-up as PDF",
        "err_followup_pdf": "Error generating the follow-up PDF: {e}",
        "footer_text": "CureMyBill — a helper tool, not a substitute for legal or medical advice.",
        "step_labels": ["1. Upload", "2. Analysis", "3. Comparison", "4. Letter"],
        "badge_no_storage": "🗑️ Your file isn't stored after your session ends",
        "badge_https": "🔒 Encrypted connection (HTTPS)",
        "badge_free_scan": "💳 No credit card required for the free scan",
        "demo_button": "▶️ Try with a sample bill",
        "demo_caption": "See how it works instantly, no upload needed.",
        "faq_title": "❓ Frequently asked questions",
        "faq_q1": "Is it legal to dispute my bill using Medicare rates?",
        "faq_a1": (
            "Yes. Medicare rates are a public, government-published benchmark. "
            "Citing them in a letter to request an itemized review and "
            "justification of charges is a normal, lawful practice — this "
            "letter is a request for information and review, not a legal claim."
        ),
        "faq_q2": "Will this affect my credit score?",
        "faq_a2": (
            "Disputing a bill directly with a hospital's billing department "
            "typically pauses collections while the review is pending, which "
            "can protect your credit rather than harm it. Policies vary by "
            "provider, so always confirm directly with the hospital's billing "
            "office."
        ),
        "faq_q3": "How long do hospitals usually take to respond?",
        "faq_a3": (
            "It varies, but the letter requests a written response within 30 "
            "days. If you don't hear back, our Pro plan includes a follow-up "
            "letter template to escalate the request."
        ),
        "faq_q4": "Is my personal and medical information kept private?",
        "faq_a4": (
            "Your bill is processed to extract the billing data needed to "
            "generate your letter, and is not stored after your session ends. "
            "We don't sell or share your data with third parties."
        ),
        "faq_q5": "Do I need a lawyer to use this?",
        "faq_a5": (
            "No. CureMyBill generates a request for itemized review, which "
            "you can send yourself. It is not legal advice and doesn't "
            "replace an attorney for complex disputes or legal action."
        ),
        "addon_insurance_section": "**📄 Add-on — Insurance Appeal Letter**",
        "addon_insurer_label": "Insurance company name",
        "addon_member_id_label": "Member / Policy ID",
        "addon_claim_number_label": "Claim number",
        "addon_generate_insurance_btn": "✍️ Generate the insurance appeal letter",
        "addon_insurance_generated_label": "Generated insurance appeal letter",
        "addon_download_insurance_pdf": "⬇️ Download the insurance appeal letter as PDF",
        "addon_phone_section": "**📞 Add-on — Phone Negotiation Script**",
        "addon_generate_phone_btn": "✍️ Generate the phone script",
        "addon_phone_generated_label": "Generated phone script",
        "addon_download_phone_pdf": "⬇️ Download the phone script as PDF",
    },
    "es": {
        "sidebar_api_label": "Clave API",
        "sidebar_api_placeholder": "sk-ant-...",
        "sidebar_api_help": "Tu clave nunca se guarda — permanece solo en memoria durante esta sesión.",
        "api_key_from_env": "✅ Clave API cargada desde la variable de entorno ANTHROPIC_API_KEY.",
        "settings_panel_label": "⚙️ Configuración",
        "sidebar_how_title": "**Cómo funciona**",
        "sidebar_how_steps": (
            "1. Sube una factura (PDF/imagen)\n"
            "2. Extraemos los códigos CPT y los montos\n"
            "3. Comparación con un baremo de referencia\n"
            "4. Generación de una carta de disputa\n"
        ),
        "sidebar_caption": (
            "ℹ️ El baremo incluido utiliza las tarifas nacionales oficiales "
            "de Medicare (una referencia pública y citable), no un 'precio "
            "justo' subjetivo. Las tarifas de Medicare suelen ser mucho "
            "más bajas que lo que factura un hospital — eso es normal, y "
            "es justamente lo que hace fuerte el argumento en una carta de "
            "disputa."
        ),
        "hero_title": "🩺 CureMyBill",
        "hero_subtitle": (
            "Analice su factura médica de EE. UU., detecte sobrecargos y "
            "genere una carta de disputa en segundos."
        ),
        "upload_label": "Suba su factura médica (PDF, PNG o JPG)",
        "analyze_button": "🔍 Analizar la factura",
        "err_need_api_key": "Por favor ingrese su clave API de Anthropic en la barra lateral.",
        "spinner_reading": "Leyendo la factura...",
        "err_bad_json": (
            "No se pudo procesar un resultado utilizable. Intente de "
            "nuevo, o verifique que el archivo sea legible (no demasiado "
            "borroso o torcido)."
        ),
        "err_analysis": "Error durante el análisis: {e}",
        "section_bill_info_title": "📄 Información de la factura — revise y complete si es necesario",
        "caption_bill_info": (
            "Esto se extrajo automáticamente. Corrija lo que sea "
            "necesario: se insertará tal cual en la carta final."
        ),
        "label_patient_name": "Nombre del paciente",
        "label_provider_name": "Nombre del hospital / proveedor",
        "label_provider_address": "Dirección del hospital",
        "label_account_number": "Número de cuenta / reclamo",
        "label_bill_date": "Fecha de la factura",
        "label_sender_title": "**Su información de contacto (remitente de la carta)**",
        "label_sender_name": "Su nombre completo",
        "label_sender_address": "Su dirección (calle, número)",
        "label_sender_city_state_zip": "Ciudad, Estado, Código Postal",
        "label_letter_date": "Fecha de la carta",
        "section_comparison_title": "💰 Comparación con la tarifa de Medicare",
        "stat_total_billed": "Total facturado",
        "stat_outlier_amount": "Diferencia en las líneas más sospechosas",
        "stat_outlier_count": "Líneas muy por encima de lo normal",
        "table_lock_label": "Auditoría detallada bloqueada — desbloquee abajo para ver códigos y montos exactos",
        "caption_comparison_methodology": (
            "Comparación con la tarifa nacional oficial de Medicare (una "
            "referencia pública y citable). Un hospital normalmente "
            "factura varias veces esta tarifa — 'Typical range' cubre un "
            "margen habitual (hasta ~5x Medicare). Más allá de eso, "
            "cuanto mayor la diferencia, más sólido es el argumento para "
            "una disputa."
        ),
        "section_letter_title": "✉️ Carta de disputa",
        "info_no_elevated_lines": (
            "Ninguna línea está dramáticamente fuera de rango, pero "
            "algunas están por encima del margen habitual — revise la "
            "tabla de arriba."
        ),
        "info_no_disputable_lines": "No se detectó ninguna línea significativamente fuera de rango.",
        "button_generate_letter": "✍️ Generar la carta",
        "spinner_writing_letter": "Redactando la carta...",
        "err_letter_generation": "Error al generar la carta: {e}",
        "lock_label": "🔒 Carta completa bloqueada",
        "unlock_cta": "**Desbloquee su carta para descargarla en PDF:**",
        "caption_payment_security": (
            "Pago seguro a través de Paddle (modo Sandbox — no se cobra "
            "ninguna tarjeta real hasta que la cuenta esté en Live). La "
            "descarga comienza automáticamente en su navegador justo "
            "después del pago, sin recargar la página."
        ),
        "success_unlocked": "✅ Acceso desbloqueado — plan {plan}.",
        "label_letter_generated": "Carta generada",
        "download_button_pdf": "⬇️ Descargar la carta en PDF",
        "err_pdf_generation": "Error al generar el PDF: {e}",
        "download_button_txt_fallback": "⬇️ Descargar la carta (.txt) — respaldo",
        "pro_section_title": "**📬 Opción Pro — Carta de seguimiento (si no hay respuesta en 30 días)**",
        "button_generate_followup": "✍️ Generar la carta de seguimiento",
        "err_followup_generation": "Error al generar el seguimiento: {e}",
        "label_followup_generated": "Carta de seguimiento generada",
        "download_button_followup_pdf": "⬇️ Descargar el seguimiento en PDF",
        "err_followup_pdf": "Error al generar el PDF de seguimiento: {e}",
        "footer_text": "CureMyBill — una herramienta de ayuda, no sustituye el asesoramiento legal o médico.",
        "step_labels": ["1. Subir", "2. Análisis", "3. Comparación", "4. Carta"],
        "badge_no_storage": "🗑️ Su archivo no se guarda después de su sesión",
        "badge_https": "🔒 Conexión cifrada (HTTPS)",
        "badge_free_scan": "💳 No se requiere tarjeta de crédito para el escaneo gratuito",
        "demo_button": "▶️ Probar con una factura de ejemplo",
        "demo_caption": "Vea cómo funciona al instante, sin necesidad de subir nada.",
        "faq_title": "❓ Preguntas frecuentes",
        "faq_q1": "¿Es legal disputar mi factura usando las tarifas de Medicare?",
        "faq_a1": (
            "Sí. Las tarifas de Medicare son una referencia pública publicada "
            "por el gobierno. Citarlas en una carta para solicitar una "
            "revisión detallada y una justificación de los cargos es una "
            "práctica normal y legal — esta carta es una solicitud de "
            "información y revisión, no una demanda legal."
        ),
        "faq_q2": "¿Afectará esto mi puntaje de crédito?",
        "faq_a2": (
            "Disputar una factura directamente con el departamento de "
            "facturación del hospital normalmente pausa el proceso de "
            "cobranza mientras se revisa, lo cual puede proteger su crédito "
            "en lugar de perjudicarlo. Las políticas varían según el "
            "proveedor, así que confirme siempre directamente con el "
            "hospital."
        ),
        "faq_q3": "¿Cuánto tiempo tardan los hospitales en responder normalmente?",
        "faq_a3": (
            "Varía, pero la carta solicita una respuesta por escrito dentro "
            "de 30 días. Si no recibe respuesta, nuestro plan Pro incluye una "
            "plantilla de carta de seguimiento para escalar la solicitud."
        ),
        "faq_q4": "¿Se mantiene privada mi información personal y médica?",
        "faq_a4": (
            "Su factura se procesa para extraer los datos de facturación "
            "necesarios para generar su carta, y no se guarda después de que "
            "termine su sesión. No vendemos ni compartimos sus datos con "
            "terceros."
        ),
        "faq_q5": "¿Necesito un abogado para usar esto?",
        "faq_a5": (
            "No. CureMyBill genera una solicitud de revisión detallada, que "
            "usted mismo puede enviar. Esto no es asesoramiento legal y no "
            "reemplaza a un abogado en disputas complejas o acciones legales."
        ),
        "addon_insurance_section": "**📄 Complemento — Carta de Apelación al Seguro**",
        "addon_insurer_label": "Nombre de la aseguradora",
        "addon_member_id_label": "ID de miembro / póliza",
        "addon_claim_number_label": "Número de reclamo",
        "addon_generate_insurance_btn": "✍️ Generar la carta de apelación al seguro",
        "addon_insurance_generated_label": "Carta de apelación al seguro generada",
        "addon_download_insurance_pdf": "⬇️ Descargar la carta de apelación en PDF",
        "addon_phone_section": "**📞 Complemento — Guion de Negociación Telefónica**",
        "addon_generate_phone_btn": "✍️ Generar el guion telefónico",
        "addon_phone_generated_label": "Guion telefónico generado",
        "addon_download_phone_pdf": "⬇️ Descargar el guion telefónico en PDF",
    },
}


def T(key: str) -> str:
    """Fetch a UI string in the currently selected app language."""
    lang = st.session_state.get("app_lang", "en")
    return UI_TEXT.get(lang, UI_TEXT["en"]).get(key, UI_TEXT["en"].get(key, key))


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def svg_icon(name: str, size: int = 22, color: str = "currentColor") -> str:
    """Small inline line-icons (Heroicons-inspired) to replace emoji in
    structural UI elements — headers, badges, step indicators."""
    attrs = (
        f'width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round"'
    )
    icons = {
        "shield": f'<svg {attrs}><path d="M12 3l7 3v6c0 4.5-3 8-7 9-4-1-7-4.5-7-9V6l7-3z"/><path d="M9 12l2 2 4-4"/></svg>',
        "lock": f'<svg {attrs}><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V7a4 4 0 018 0v4"/></svg>',
        "document": f'<svg {attrs}><path d="M7 3h7l4 4v13a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1z"/><path d="M14 3v4h4"/><path d="M9 13h6M9 17h6"/></svg>',
        "upload": f'<svg {attrs}><path d="M12 16V4M12 4l-4 4M12 4l4 4"/><path d="M4 16v3a2 2 0 002 2h12a2 2 0 002-2v-3"/></svg>',
        "search": f'<svg {attrs}><circle cx="11" cy="11" r="6"/><path d="M20 20l-4-4"/></svg>',
        "scale": f'<svg {attrs}><path d="M12 3v18M7 7l-4 6a4 4 0 008 0l-4-6zM17 7l-4 6a4 4 0 008 0l-4-6z"/><path d="M5 21h14"/></svg>',
        "mail": f'<svg {attrs}><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>',
        "phone": f'<svg {attrs}><path d="M6 3h3l2 5-2 1a11 11 0 005 5l1-2 5 2v3a2 2 0 01-2 2A16 16 0 014 5a2 2 0 012-2z"/></svg>',
        "question": f'<svg {attrs}><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 015 .5c0 1.5-2.5 2-2.5 3.5"/><path d="M12 17h.01"/></svg>',
        "settings": f'<svg {attrs}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.34 1.87l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.7 1.7 0 00-1.87-.34 1.7 1.7 0 00-1.04 1.56V21a2 2 0 11-4 0v-.09A1.7 1.7 0 008.96 19.4a1.7 1.7 0 00-1.87.34l-.06.06a2 2 0 11-2.83-2.83l.06-.06A1.7 1.7 0 004.6 15a1.7 1.7 0 00-1.56-1.04H3a2 2 0 110-4h.09A1.7 1.7 0 004.6 8.96a1.7 1.7 0 00-.34-1.87l-.06-.06a2 2 0 112.83-2.83l.06.06A1.7 1.7 0 008.96 4.6a1.7 1.7 0 001.04-1.56V3a2 2 0 114 0v.09a1.7 1.7 0 001.04 1.56 1.7 1.7 0 001.87-.34l.06-.06a2 2 0 112.83 2.83l-.06.06A1.7 1.7 0 0019.4 8.96a1.7 1.7 0 001.56 1.04H21a2 2 0 110 4h-.09a1.7 1.7 0 00-1.56 1.04z"/></svg>',
    }
    return icons.get(name, "")


def render_section_header(icon_name: str, text: str, emoji_to_strip: str = "") -> None:
    """Consistent section header: SVG icon + text, replacing an emoji prefix."""
    clean_text = text.replace(emoji_to_strip, "").strip() if emoji_to_strip else text
    st.markdown(
        f"""<h3 style="display:flex; align-items:center; gap:8px; margin:0 0 0.8rem 0;">
        <span class="mb-icon-badge">{svg_icon(icon_name, 22, '#2563eb')}</span>
        {clean_text}
        </h3>""",
        unsafe_allow_html=True,
    )


def get_configured_api_key() -> str:
    """Look for the Anthropic API key in, in order:
    1. Streamlit Cloud's Secrets (Settings > Secrets, once deployed)
    2. A local ANTHROPIC_API_KEY environment variable (local dev)
    Returns "" if neither is set, so the user can type it in manually.
    """
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass  # No secrets.toml locally — that's expected, not an error.
    return os.getenv("ANTHROPIC_API_KEY", "")


def render_step_bar(current_step: int) -> None:
    """Small visual progress bar across the 4 stages of the journey."""
    steps = T("step_labels")
    step_icons = ["upload", "search", "scale", "mail"]
    html = '<div class="mb-steps">'
    for i, (label, icon_name) in enumerate(zip(steps, step_icons), start=1):
        cls = "done" if i < current_step else ("active" if i == current_step else "")
        html += (
            f'<div class="mb-step {cls}">'
            f'<div class="mb-icon-badge">{svg_icon(icon_name, 18)}</div>'
            f"<div>{label}</div>"
            f"</div>"
        )
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def render_count_up_stat(value_str: str, label: str, css_class: str = "mb-stat") -> None:
    """Render a stat card whose number animates counting up from 0 on first render."""
    import re as _re

    cleaned = value_str.replace(",", "")
    match = _re.search(r"[\d.]+", cleaned)
    target = float(match.group()) if match else 0
    prefix = cleaned[: match.start()] if match else ""
    suffix = cleaned[match.end() :] if match else cleaned
    uid = f"cu{abs(hash((value_str, label))) % 100000}"
    components.html(
        f"""
        <div style="font-family: -apple-system, 'Segoe UI', Roboto, sans-serif;">
          <div class="{css_class}" style="border-radius:12px; padding:1rem; text-align:center;">
            <div style="font-size:1.6rem; font-weight:700;" id="{uid}">{prefix}0{suffix}</div>
            <div style="font-size:0.8rem; color:#6b7280; margin-top:0.2rem;">{label}</div>
          </div>
        </div>
        <script>
          (function() {{
            var el = document.getElementById("{uid}");
            var target = {target};
            var prefix = "{prefix}";
            var suffix = "{suffix}";
            var start = 0;
            var duration = 700;
            var startTime = null;
            function step(ts) {{
              if (!startTime) startTime = ts;
              var progress = Math.min((ts - startTime) / duration, 1);
              var current = start + (target - start) * progress;
              el.textContent = prefix + current.toLocaleString(undefined, {{maximumFractionDigits: 2}}) + suffix;
              if (progress < 1) requestAnimationFrame(step);
            }}
            requestAnimationFrame(step);
          }})();
        </script>
        """,
        height=100,
    )


@st.cache_data
def load_fee_schedule() -> pd.DataFrame:
    df = pd.read_csv(FEE_SCHEDULE_PATH, dtype={"cpt_code": str})
    df["cpt_code"] = df["cpt_code"].str.strip().str.upper()
    return df


def file_to_content_block(uploaded_file) -> dict:
    """Turn a Streamlit UploadedFile into an Anthropic API content block."""
    raw_bytes = uploaded_file.getvalue()
    b64 = base64.b64encode(raw_bytes).decode("utf-8")
    mime = uploaded_file.type or ""

    if mime == "application/pdf" or uploaded_file.name.lower().endswith(".pdf"):
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": b64,
            },
        }
    else:
        # Normalise common image mime types
        if mime not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
            mime = "image/png"
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime,
                "data": b64,
            },
        }


EXTRACTION_SYSTEM_PROMPT = """You are a medical billing data extraction assistant.
You will be shown a US medical bill (image or PDF). Extract every billable line
item you can find.

Respond with ONLY a valid JSON object, no markdown fences, no commentary,
matching exactly this schema:

{
  "provider_name": string or null,
  "provider_address": string or null,
  "patient_name": string or null,
  "bill_date": string or null,
  "account_number": string or null,
  "line_items": [
    {
      "cpt_code": string or null,
      "description": string,
      "quantity": number,
      "billed_amount": number
    }
  ],
  "total_billed": number or null
}

Rules:
- account_number is the account, claim, invoice, or reference number printed
  on the bill (labelled things like "Account #", "Claim Number", "Invoice No",
  "Patient Account"), or null if none is visible.
- provider_address is the hospital/clinic's mailing or billing address as
  printed on the bill, or null if not visible.
- cpt_code should be the 5-character CPT/HCPCS code exactly as printed (letters
  uppercase), or null if none is visible for that line.
- billed_amount is the amount charged for that line item in US dollars, as a
  plain number (no $ sign, no commas).
- If a field is not present on the bill, use null.
- Do not invent CPT codes that are not shown or clearly implied by the bill.
- Output must be valid JSON and nothing else.
"""


def extract_bill_data(client: Anthropic, file_block: dict) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    file_block,
                    {
                        "type": "text",
                        "text": "Extract the billing data from this medical bill as JSON.",
                    },
                ],
            }
        ],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text)


def compare_to_schedule(line_items: list[dict], fee_schedule: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for item in line_items:
        cpt = (item.get("cpt_code") or "").strip().upper()
        billed = item.get("billed_amount") or 0
        qty = item.get("quantity") or 1
        match = fee_schedule[fee_schedule["cpt_code"] == cpt]

        if not match.empty:
            standard = float(match.iloc[0]["standard_price"])
            diff = billed - standard
            pct = (diff / standard * 100) if standard else None
            ratio = (billed / standard) if standard else None

            # Hospital charges are always well above the Medicare rate by design —
            # Medicare is a floor, not a "fair price". We flag based on how far
            # above the *typical* hospital markup range (roughly 2-5x Medicare)
            # a charge falls, rather than flagging any markup at all.
            if ratio is None:
                status = "No reference"
            elif ratio <= 5:
                status = "Typical range"
            elif ratio <= 10:
                status = "Above typical markup"
            else:
                status = "Well above typical — worth disputing"
        else:
            standard, diff, pct, status = None, None, None, "No reference"

        rows.append(
            {
                "CPT Code": cpt or "—",
                "Description": item.get("description", ""),
                "Qty": qty,
                "Billed ($)": billed,
                "Medicare Rate ($)": standard,
                "Difference ($)": diff,
                "Difference (%)": round(pct, 1) if pct is not None else None,
                "Status": status,
            }
        )
    return pd.DataFrame(rows)


LETTER_SYSTEM_PROMPT = """You are an expert patient-advocacy assistant who writes
clear, firm, professional medical bill dispute letters on behalf of US patients.

Write in formal English business-letter style. Be factual and assertive but
polite. Cite the specific CPT codes and dollar amounts provided, comparing the
billed amount to the official Medicare national reimbursement rate for that
code. Frame this as "billed at X times the Medicare rate" rather than
asserting the hospital committed fraud or overcharged unfairly — Medicare
rates are a documented public benchmark, not a legal ceiling on what a
provider may charge, so the letter should request justification and an
itemized review rather than assert wrongdoing. Ask explicitly for an itemized
re-review, a corrected bill, and a written response within 30 days. Do not
invent facts beyond what is given. Keep it to one page.
"""


def generate_dispute_letter(
    client: Anthropic,
    sender_name: str,
    sender_address: str,
    sender_city_state_zip: str,
    provider_name: str,
    provider_address: str,
    patient_name: str,
    account_number: str,
    bill_date: str,
    letter_date: str,
    disputed_df: pd.DataFrame,
) -> str:
    items_text = "\n".join(
        f"- CPT {row['CPT Code']}: {row['Description']} — billed ${row['Billed ($)']:.2f}, "
        f"Medicare national rate ${row['Medicare Rate ($)']:.2f} "
        f"(+{row['Difference (%)']}% above the Medicare rate)"
        for _, row in disputed_df.iterrows()
    )

    def _val(v, placeholder):
        v = (v or "").strip()
        return v if v else placeholder

    user_prompt = f"""Write a medical bill dispute letter using these EXACT details.
Do not use bracket placeholders for any field listed below — use the real
value given. Only use a bracket placeholder (e.g. [Account/Claim Number]) for
a field that is explicitly marked as "not provided".

Sender (the person sending this letter):
Name: {_val(sender_name, "[Your Name]")}
Address: {_val(sender_address, "[Your Address] — not provided")}
City/State/ZIP: {_val(sender_city_state_zip, "[City, State ZIP] — not provided")}
Letter date: {_val(letter_date, "[Date]")}

Recipient / provider:
Provider name: {_val(provider_name, "[Provider Name] — not provided")}
Provider address: {_val(provider_address, "[Provider Address] — not provided")}

Patient and account details:
Patient name: {_val(patient_name, "[Patient Name] — not provided")}
Account/Claim number: {_val(account_number, "[Account/Claim Number] — not provided")}
Bill date: {_val(bill_date, "[Bill Date] — not provided")}

Disputed line items (billed amount vs. the official Medicare national reimbursement rate for that CPT code):
{items_text}

The letter should request an itemized review, ask the provider to justify the
charges against standard pricing benchmarks (e.g. Medicare or regional rates),
and request a corrected invoice or written explanation within 30 days.
Format it as a ready-to-print business letter: sender block, date, recipient
block, subject line, body, closing, and signature line with the sender's name.
"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=LETTER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


FOLLOWUP_SYSTEM_PROMPT = """You are an expert patient-advocacy assistant writing a
FOLLOW-UP letter because the original dispute letter received no response within
30 days. Keep it formal but firmer in tone: reference the original letter and its
date, note that no response was received within the requested 30-day window, and
escalate the requested next steps — mention that the patient may file a complaint
with the state insurance commissioner, the hospital's patient advocacy or
compliance office, and/or the Consumer Financial Protection Bureau if billing
collections are involved. Do not threaten legal action explicitly or invent facts.
Keep it to one page.
"""


def generate_followup_letter(client: Anthropic, original_letter: str, original_date: str) -> str:
    user_prompt = f"""Here is the original dispute letter, sent on {original_date or "[original date]"},
which has not received a response within 30 days:

---
{original_letter}
---

Write a follow-up letter referencing this original letter, its date, and the
lack of response, escalating the request for resolution as described in your
instructions.
"""
    response = client.messages.create(
        model=MODEL,
        max_tokens=1200,
        system=FOLLOWUP_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


INSURANCE_APPEAL_SYSTEM_PROMPT = """You are an expert patient-advocacy assistant
writing a FORMAL APPEAL LETTER to a health insurance company (not the hospital).
This is used when the hospital's charges are high partly because the insurer
denied or underpaid a claim. Write in formal English business-letter style.
Reference the patient's policy/member ID and claim number when given. Ask the
insurer to reprocess the claim, provide a written explanation for any denial
or reduced payment, and clarify in-network/out-of-network status if relevant.
Mention the patient's right to a formal internal appeal and, if unresolved, an
external review, without asserting specific legal violations. Do not invent
facts beyond what is given. Keep it to one page.
"""


def generate_insurance_appeal_letter(
    client: Anthropic,
    sender_name: str,
    sender_address: str,
    sender_city_state_zip: str,
    insurer_name: str,
    member_id: str,
    claim_number: str,
    patient_name: str,
    disputed_df: pd.DataFrame,
) -> str:
    def _val(v, placeholder):
        v = (v or "").strip()
        return v if v else placeholder

    items_text = "\n".join(
        f"- CPT {row['CPT Code']}: {row['Description']} — billed ${row['Billed ($)']:.2f}, "
        f"Medicare national rate ${row['Medicare Rate ($)']:.2f}"
        for _, row in disputed_df.iterrows()
    )

    user_prompt = f"""Write an insurance appeal letter using these details:

Sender: {_val(sender_name, "[Your Name]")}
Address: {_val(sender_address, "[Your Address]")}
City/State/ZIP: {_val(sender_city_state_zip, "[City, State ZIP]")}

Insurance company: {_val(insurer_name, "[Insurance Company Name]")}
Member/Policy ID: {_val(member_id, "[Member ID]")}
Claim number: {_val(claim_number, "[Claim Number]")}
Patient name: {_val(patient_name, "[Patient Name]")}

Disputed / underpaid line items:
{items_text}

Request reprocessing of the claim, a written explanation of any denial or
reduced payment, and clarification of network status if relevant.
"""
    response = client.messages.create(
        model=MODEL,
        max_tokens=1200,
        system=INSURANCE_APPEAL_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


PHONE_SCRIPT_SYSTEM_PROMPT = """You are an expert patient-advocacy assistant
writing a SHORT, PRACTICAL PHONE SCRIPT (not a letter) that a patient can read
almost word-for-word when calling a hospital billing department. Plain,
conversational English, organized in clear steps: opening the call, stating
the purpose, citing the specific overbilled line items, asking about a
self-pay/cash discount or financial assistance program, and what to say if
the representative pushes back. Keep it concise and actionable — this is a
cheat sheet, not a formal document. Do not invent facts beyond what is given.
"""


def generate_phone_script(
    client: Anthropic, patient_name: str, account_number: str, disputed_df: pd.DataFrame
) -> str:
    items_text = "\n".join(
        f"- CPT {row['CPT Code']}: {row['Description']} — billed ${row['Billed ($)']:.2f}, "
        f"Medicare national rate ${row['Medicare Rate ($)']:.2f}"
        for _, row in disputed_df.iterrows()
    )
    user_prompt = f"""Write a phone negotiation script for this patient calling
the hospital billing department:

Patient name: {patient_name or "[Patient Name]"}
Account number: {account_number or "[Account Number]"}

Overbilled line items to mention:
{items_text}

Include a line asking about a self-pay/cash discount and any financial
assistance / charity care program the hospital may offer.
"""
    response = client.messages.create(
        model=MODEL,
        max_tokens=900,
        system=PHONE_SCRIPT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


# Closing phrases after which we leave room for a handwritten signature
_LETTER_CLOSINGS = ("sincerely", "regards", "respectfully", "best regards", "yours truly")


def generate_pdf_letter(letter_text: str) -> bytes:
    """Render the dispute letter as a clean, printable business-letter PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=LETTER_PAGESIZE,
        topMargin=1 * inch,
        bottomMargin=1 * inch,
        leftMargin=1 * inch,
        rightMargin=1 * inch,
        title="Medical Bill Dispute Letter",
    )

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "LetterBody",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=11,
        leading=16,
        spaceAfter=12,
    )

    story = []
    paragraphs = [p.strip() for p in letter_text.strip().split("\n\n") if p.strip()]

    for para in paragraphs:
        # Preserve intentional line breaks inside a paragraph (e.g. address blocks)
        safe_html = xml_escape(para).replace("\n", "<br/>")
        story.append(Paragraph(safe_html, body_style))

        first_line = para.strip().split("\n")[0].strip().lower().rstrip(",")
        if first_line in _LETTER_CLOSINGS:
            story.append(Spacer(1, 50))  # room to sign by hand before printing the name

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def render_paddle_pricing_widget(pdf_base64: str, lang: str = "en") -> str:
    """Build the HTML/JS for the Paddle.js checkout overlay with 2 pricing cards.

    Important: instead of relying on Paddle's `successUrl` (which does a full
    browser navigation and wipes Streamlit's in-memory session state, losing
    the extracted bill + generated letter), the already-rendered PDF is
    embedded as base64 directly in this widget. On a successful payment
    (`checkout.completed` event), the browser downloads it immediately via a
    Blob — no page reload, no dependency on Streamlit noticing the payment.
    """
    widget_text = {
        "en": {
            "recommended": "Recommended",
            "standard_title": "Standard",
            "standard_li1": "Complete dispute letter in PDF",
            "standard_li2": "Ready to print and send",
            "standard_btn": "Unlock — $19",
            "pro_title": "Pro",
            "pro_li1": "Everything included in Standard",
            "pro_li2": "+ Follow-up letter if no response within 30 days",
            "pro_btn": "Unlock — $34",
            "success_msg": "✅ Payment confirmed — the download started automatically.",
            "fallback_link": "Click here if the download didn't start",
            "addons_title": "Add extra help to your order:",
            "addon_insurance": "Insurance Appeal Letter (+$14) — for a denied or underpaid claim",
            "addon_phone": "Phone Negotiation Script (+$9) — what to say when you call billing",
        },
        "es": {
            "recommended": "Recomendado",
            "standard_title": "Standard",
            "standard_li1": "Carta de disputa completa en PDF",
            "standard_li2": "Lista para imprimir y enviar",
            "standard_btn": "Desbloquear — $19",
            "pro_title": "Pro",
            "pro_li1": "Todo lo incluido en Standard",
            "pro_li2": "+ Carta de seguimiento si no hay respuesta en 30 días",
            "pro_btn": "Desbloquear — $34",
            "success_msg": "✅ Pago confirmado — la descarga comenzó automáticamente.",
            "fallback_link": "Haga clic aquí si la descarga no comenzó",
            "addons_title": "Agregue ayuda adicional a su pedido:",
            "addon_insurance": "Carta de Apelación al Seguro (+$14) — para un reclamo denegado o pagado de menos",
            "addon_phone": "Guion de Negociación Telefónica (+$9) — qué decir al llamar a facturación",
        },
    }
    w = widget_text.get(lang, widget_text["en"])
    return f"""
<style>
  * {{ box-sizing: border-box; }}
  .mb-plans {{
    display: flex;
    gap: 16px;
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  }}
  .mb-plan {{
    flex: 1;
    border: 1px solid #e5e7eb;
    border-radius: 14px;
    padding: 1.2rem 1.3rem;
    background: white;
  }}
  .mb-plan.pro {{ border: 2px solid #2563eb; position: relative; }}
  .mb-plan.pro::before {{
    content: "{w['recommended']}";
    position: absolute; top: -11px; left: 16px;
    background: #2563eb; color: white; font-size: 0.7rem;
    font-weight: 700; padding: 2px 10px; border-radius: 999px;
  }}
  .mb-plan h4 {{ margin: 0 0 4px 0; font-size: 1rem; color: #111827; }}
  .mb-plan .price {{ font-size: 1.7rem; font-weight: 800; color: #111827; margin-bottom: 8px; }}
  .mb-plan ul {{ margin: 0 0 14px 0; padding-left: 18px; font-size: 0.85rem; color: #4b5563; }}
  .mb-plan li {{ margin-bottom: 4px; }}
  .mb-plan button {{
    width: 100%; padding: 0.6rem; border: none; border-radius: 10px;
    font-weight: 700; cursor: pointer; font-size: 0.9rem;
  }}
  .mb-plan.standard button {{ background: #f3f4f6; color: #111827; }}
  .mb-plan.pro button {{ background: #2563eb; color: white; }}
  #mb-success {{
    display: none; margin-top: 16px; padding: 1rem 1.2rem;
    background: #ecfdf5; border: 1px solid #10b981; border-radius: 12px;
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  }}
  #mb-success a {{
    display: inline-block; margin-top: 8px; background: #059669; color: white;
    padding: 0.5rem 1rem; border-radius: 8px; text-decoration: none; font-weight: 700;
  }}
</style>

<div class="mb-plans">
  <div class="mb-plan standard">
    <h4>{w['standard_title']}</h4>
    <div class="price">$19</div>
    <ul>
      <li>{w['standard_li1']}</li>
      <li>{w['standard_li2']}</li>
    </ul>
    <button onclick="mbCheckout('{PADDLE_PRICE_STANDARD}', 'standard')">{w['standard_btn']}</button>
  </div>
  <div class="mb-plan pro">
    <h4>{w['pro_title']}</h4>
    <div class="price">$34</div>
    <ul>
      <li>{w['pro_li1']}</li>
      <li>{w['pro_li2']}</li>
    </ul>
    <button onclick="mbCheckout('{PADDLE_PRICE_PRO}', 'pro')">{w['pro_btn']}</button>
  </div>
</div>

<div style="margin-top:14px; font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; font-size:0.85rem; color:#374151;">
  <div style="font-weight:700; margin-bottom:6px;">{w['addons_title']}</div>
  <label style="display:block; margin-bottom:4px;">
    <input type="checkbox" id="mb-addon-insurance" /> {w['addon_insurance']}
  </label>
  <label style="display:block;">
    <input type="checkbox" id="mb-addon-phone" /> {w['addon_phone']}
  </label>
</div>

<div id="mb-success">
  {w['success_msg']}
  <br/>
  <a id="mb-fallback-link" href="#" download="dispute_letter.pdf">{w['fallback_link']}</a>
</div>

<script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script>
<script>
  var MB_PDF_BASE64 = "{pdf_base64}";
  var mbSelectedPlan = "standard";
  var mbSelectedAddons = [];

  function mbDownloadPdf() {{
    var byteChars = atob(MB_PDF_BASE64);
    var byteNumbers = new Array(byteChars.length);
    for (var i = 0; i < byteChars.length; i++) {{
      byteNumbers[i] = byteChars.charCodeAt(i);
    }}
    var byteArray = new Uint8Array(byteNumbers);
    var blob = new Blob([byteArray], {{ type: "application/pdf" }});
    var url = URL.createObjectURL(blob);

    var fallback = document.getElementById("mb-fallback-link");
    fallback.href = url;

    var link = document.createElement("a");
    link.href = url;
    link.download = "dispute_letter.pdf";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }}

  Paddle.Environment.set("{PADDLE_ENVIRONMENT}");
  Paddle.Initialize({{
    token: "{PADDLE_CLIENT_TOKEN}",
    eventCallback: function(evt) {{
      if (evt.name === "checkout.completed") {{
        mbDownloadPdf();
        document.getElementById("mb-success").style.display = "block";

        // Best-effort: try to let the Streamlit page know payment succeeded,
        // without a hard navigation (which would wipe its session state).
        try {{
          var base = window.top.location.origin + window.top.location.pathname;
          var addonsParam = mbSelectedAddons.length ? mbSelectedAddons.join(",") : "";
          var newUrl = base + "?paid=1&plan=" + mbSelectedPlan + "&addons=" + addonsParam;
          window.top.history.pushState({{}}, "", newUrl);
          window.top.dispatchEvent(new PopStateEvent("popstate"));
        }} catch (e) {{ /* not critical — the PDF already downloaded above */ }}
      }}
    }}
  }});

  function mbCheckout(priceId, plan) {{
    mbSelectedPlan = plan;
    var items = [{{ priceId: priceId, quantity: 1 }}];
    mbSelectedAddons = [];

    if (document.getElementById("mb-addon-insurance").checked) {{
      items.push({{ priceId: "{PADDLE_PRICE_INSURANCE_APPEAL}", quantity: 1 }});
      mbSelectedAddons.push("insurance");
    }}
    if (document.getElementById("mb-addon-phone").checked) {{
      items.push({{ priceId: "{PADDLE_PRICE_PHONE_SCRIPT}", quantity: 1 }});
      mbSelectedAddons.push("phone");
    }}

    // No settings.successUrl here on purpose — see function docstring above.
    Paddle.Checkout.open({{ items: items }});
  }}
</script>
"""


# --------------------------------------------------------------------------
# Top bar + settings (replaces the old technical sidebar)
# --------------------------------------------------------------------------

top_left, top_right = st.columns([3, 1])
with top_left:
    st.markdown(
        f"""
        <div class="mb-brand">
            <span class="mb-icon-badge">{svg_icon('shield', 24, '#2563eb')}</span>
            CureMyBill
        </div>
        """,
        unsafe_allow_html=True,
    )
with top_right:
    lang_options = ["en", "es"]
    new_lang = st.selectbox(
        "Language",
        options=lang_options,
        index=lang_options.index(st.session_state.get("app_lang", "en")),
        format_func=lambda v: "English (US)" if v == "en" else "Español",
        label_visibility="collapsed",
    )
    if new_lang != st.session_state.get("app_lang"):
        st.session_state.app_lang = new_lang
        st.rerun()

with st.expander(T("settings_panel_label")):
    _env_api_key = get_configured_api_key()
    if _env_api_key:
        st.success(T("api_key_from_env"))
        api_key = _env_api_key
    else:
        api_key = st.text_input(
            T("sidebar_api_label"),
            type="password",
            placeholder=T("sidebar_api_placeholder"),
            help=T("sidebar_api_help"),
        )
    st.markdown(T("sidebar_how_title") + "\n\n" + T("sidebar_how_steps"))
    st.caption(T("sidebar_caption"))

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

st.markdown(
    f"""
    <div class="mb-hero">
        <h1 style="display:flex; align-items:center; gap:10px;">
            <span class="mb-icon-badge">{svg_icon('shield', 30, 'white')}</span>
            {T('hero_title').replace('🩺', '').strip()}
        </h1>
        <p>{T('hero_subtitle')}</p>
    </div>
    """,
    unsafe_allow_html=True,
)

if "extracted" not in st.session_state:
    st.session_state.extracted = None
if "comparison_df" not in st.session_state:
    st.session_state.comparison_df = None
if "letter" not in st.session_state:
    st.session_state.letter = None
if "payment_confirmed" not in st.session_state:
    st.session_state.payment_confirmed = False
if "paid_plan" not in st.session_state:
    st.session_state.paid_plan = None
if "followup_letter" not in st.session_state:
    st.session_state.followup_letter = None
if "purchased_addons" not in st.session_state:
    st.session_state.purchased_addons = []
if "insurance_appeal_letter" not in st.session_state:
    st.session_state.insurance_appeal_letter = None
if "phone_script" not in st.session_state:
    st.session_state.phone_script = None
if "audit_id" not in st.session_state:
    st.session_state.audit_id = None


def sync_audit_to_store() -> None:
    """Persist the current analysis to the local audit store so a page
    refresh (or a hard redirect we don't control) can restore it via the
    `audit` URL parameter instead of losing everything."""
    if not st.session_state.get("audit_id"):
        return
    comparison_df = st.session_state.get("comparison_df")
    audit_store.save_audit(
        st.session_state.audit_id,
        {
            "extracted": st.session_state.get("extracted"),
            "comparison_records": (
                comparison_df.to_dict(orient="records") if comparison_df is not None else None
            ),
            "letter": st.session_state.get("letter"),
            "followup_letter": st.session_state.get("followup_letter"),
            "insurance_appeal_letter": st.session_state.get("insurance_appeal_letter"),
            "phone_script": st.session_state.get("phone_script"),
        },
    )


# Restore a previous analysis from the audit store if we land here with
# ?audit=<id> and don't already have data in memory (e.g. after a manual
# page refresh, which normally wipes Streamlit's session state).
_url_audit_id = st.query_params.get("audit")
if _url_audit_id and st.session_state.extracted is None:
    _restored = audit_store.load_audit(_url_audit_id)
    if _restored:
        st.session_state.audit_id = _url_audit_id
        st.session_state.extracted = _restored.get("extracted")
        _records = _restored.get("comparison_records")
        st.session_state.comparison_df = pd.DataFrame(_records) if _records else None
        st.session_state.letter = _restored.get("letter")
        st.session_state.followup_letter = _restored.get("followup_letter")
        st.session_state.insurance_appeal_letter = _restored.get("insurance_appeal_letter")
        st.session_state.phone_script = _restored.get("phone_script")
        # The store is the source of truth for payment once a real webhook
        # (see webhook_server.py) has confirmed it server-side.
        _paid, _plan, _addons = audit_store.is_paid(_url_audit_id)
        if _paid:
            st.session_state.payment_confirmed = True
            st.session_state.paid_plan = _plan
            st.session_state.purchased_addons = _addons

# Paddle redirects back here with ?paid=1&plan=standard|pro after a successful
# Sandbox checkout (see render_paddle_pricing_widget). This client-side signal
# is a fallback for local Sandbox testing — it can be spoofed by anyone who
# edits the URL by hand. Once webhook_server.py is deployed and receiving
# real Paddle notifications, audit_store.is_paid() above becomes the actual
# source of truth and this block becomes a convenience fallback only.
if st.query_params.get("paid") == "1":
    st.session_state.payment_confirmed = True
    st.session_state.paid_plan = st.query_params.get("plan", "standard")
    addons_param = st.query_params.get("addons", "")
    st.session_state.purchased_addons = [a for a in addons_param.split(",") if a]
    if st.session_state.audit_id:
        audit_store.mark_paid(
            st.session_state.audit_id, st.session_state.paid_plan, st.session_state.purchased_addons
        )
    st.query_params.pop("paid", None)
    st.query_params.pop("plan", None)
    st.query_params.pop("addons", None)

# Détermine l'étape actuelle du parcours pour la barre de progression
if st.session_state.get("letter"):
    _current_step = 4
elif st.session_state.get("extracted"):
    _current_step = 3
elif "uploaded_this_run" in st.session_state and st.session_state.uploaded_this_run:
    _current_step = 2
else:
    _current_step = 1
render_step_bar(_current_step)

with st.container(border=True):
    uploaded_file = st.file_uploader(
        T("upload_label"),
        type=["pdf", "png", "jpg", "jpeg"],
    )
    st.session_state.uploaded_this_run = uploaded_file is not None
    col_a, col_b = st.columns([2, 1])
    with col_a:
        analyze_clicked = st.button(T("analyze_button"), type="primary", disabled=not uploaded_file)
    with col_b:
        demo_clicked = st.button(T("demo_button"))
    st.caption(T("demo_caption"))
    st.markdown(
        f"""
        <div style="display:flex; gap:14px; flex-wrap:wrap; margin-top:0.6rem;
                    font-size:0.78rem; color:#6b7280;">
            <span>{T('badge_no_storage')}</span>
            <span>{T('badge_https')}</span>
            <span>{T('badge_free_scan')}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

if demo_clicked:
    sample_extracted = {
        "provider_name": "St. Jude Community Hospital",
        "provider_address": "123 Health Ave, Austin, TX 78701",
        "patient_name": "John Doe",
        "bill_date": "05/12/2026",
        "account_number": "987654321",
        "line_items": [
            {
                "cpt_code": "99285",
                "description": "Emergency Department Visit, Level 5",
                "quantity": 1,
                "billed_amount": 2450.00,
            },
            {
                "cpt_code": "70450",
                "description": "CT Scan, Head/Brain without Contrast",
                "quantity": 1,
                "billed_amount": 3200.00,
            },
            {
                "cpt_code": "70450",
                "description": "CT Scan, Head/Brain without Contrast (duplicate entry)",
                "quantity": 1,
                "billed_amount": 3200.00,
            },
            {
                "cpt_code": "J7030",
                "description": "Saline IV Solution, 1000ml",
                "quantity": 1,
                "billed_amount": 380.00,
            },
            {
                "cpt_code": "96374",
                "description": "IV push, single/initial substance",
                "quantity": 1,
                "billed_amount": 180.00,
            },
        ],
        "total_billed": 9410.00,
    }
    st.session_state.extracted = sample_extracted
    fee_schedule = load_fee_schedule()
    comparison_df = compare_to_schedule(sample_extracted["line_items"], fee_schedule)
    st.session_state.comparison_df = comparison_df
    st.session_state.letter = None
    st.session_state.payment_confirmed = False
    st.session_state.paid_plan = None
    st.session_state.purchased_addons = []
    st.session_state.audit_id = audit_store.new_audit_id()
    st.query_params["audit"] = st.session_state.audit_id
    sync_audit_to_store()
    if (comparison_df["Status"] == "Well above typical — worth disputing").any():
        st.balloons()

if analyze_clicked:
    if not api_key:
        st.error(T("err_need_api_key"))
    else:
        try:
            client = Anthropic(api_key=api_key)
            with st.spinner(T("spinner_reading")):
                file_block = file_to_content_block(uploaded_file)
                extracted = extract_bill_data(client, file_block)
            st.session_state.extracted = extracted

            fee_schedule = load_fee_schedule()
            comparison_df = compare_to_schedule(extracted.get("line_items", []), fee_schedule)
            st.session_state.comparison_df = comparison_df
            st.session_state.letter = None  # reset any previous letter
            st.session_state.payment_confirmed = False  # new bill = new paywall
            st.session_state.paid_plan = None
            st.session_state.purchased_addons = []
            st.session_state.audit_id = audit_store.new_audit_id()
            st.query_params["audit"] = st.session_state.audit_id
            sync_audit_to_store()

            # Petite récompense visuelle si on a trouvé un vrai écart à contester —
            # pas à chaque analyse, seulement quand il y a vraiment de la valeur trouvée.
            if (comparison_df["Status"] == "Well above typical — worth disputing").any():
                st.balloons()
        except json.JSONDecodeError:
            st.error(T("err_bad_json"))
        except Exception as e:
            st.error(T("err_analysis").format(e=e))

# --------------------------------------------------------------------------
# Résultats
# --------------------------------------------------------------------------

if st.session_state.extracted:
    extracted = st.session_state.extracted
    df = st.session_state.comparison_df

    with st.container(border=True):
        render_section_header("document", T("section_bill_info_title"), "📄")
        st.caption(T("caption_bill_info"))

        fc1, fc2 = st.columns(2)
        with fc1:
            patient_name = st.text_input(T("label_patient_name"), value=extracted.get("patient_name") or "")
            provider_name = st.text_input(T("label_provider_name"), value=extracted.get("provider_name") or "")
            provider_address = st.text_input(
                T("label_provider_address"), value=extracted.get("provider_address") or ""
            )
        with fc2:
            account_number = st.text_input(
                T("label_account_number"), value=extracted.get("account_number") or ""
            )
            bill_date = st.text_input(T("label_bill_date"), value=extracted.get("bill_date") or "")

        st.markdown(T("label_sender_title"))
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            sender_name = st.text_input(T("label_sender_name"), value=patient_name)
        with sc2:
            sender_address = st.text_input(T("label_sender_address"))
        with sc3:
            sender_city_state_zip = st.text_input(T("label_sender_city_state_zip"))

        letter_date = st.text_input(
            T("label_letter_date"), value=pd.Timestamp.today().strftime("%B %d, %Y")
        )


    total_billed = df["Billed ($)"].sum()
    outlier_df = df[df["Status"] == "Well above typical — worth disputing"]
    elevated_df = df[df["Status"].isin(["Above typical markup", "Well above typical — worth disputing"])]
    total_outlier_amount = (
        outlier_df["Difference ($)"].clip(lower=0).sum() if not outlier_df.empty else 0
    )

    with st.container(border=True):
        render_section_header("scale", T("section_comparison_title"), "💰")

        s1, s2, s3 = st.columns(3)
        with s1:
            render_count_up_stat(f"${total_billed:,.2f}", T("stat_total_billed"))
        with s2:
            render_count_up_stat(
                f"${total_outlier_amount:,.2f}",
                T("stat_outlier_amount"),
                css_class="mb-stat danger",
            )
        with s3:
            render_count_up_stat(str(len(outlier_df)), T("stat_outlier_count"))

        st.write("")

        def highlight_status(row):
            color = {
                "Well above typical — worth disputing": "background-color: #fef2f2; color:#dc2626;",
                "Above typical markup": "background-color: #fffbeb; color:#b45309;",
                "Typical range": "background-color: #ecfdf5; color:#059669;",
                "No reference": "background-color: #f9fafb; color:#6b7280;",
            }.get(row["Status"], "")
            return [color] * len(row)

        st.markdown(
            f"""<div style="text-align:center; margin-bottom:10px;">
            <span style="font-weight:700; color:#111827; background:#f3f4f6;
                         padding:0.5rem 1rem; border-radius:999px; display:inline-flex;
                         align-items:center; gap:6px;">
                {svg_icon('lock', 16)} {T('table_lock_label')}
            </span>
            </div>""",
            unsafe_allow_html=True,
        )
        st.markdown('<span class="mb-blur-marker"></span>', unsafe_allow_html=True)
        st.dataframe(
            df.style.apply(highlight_status, axis=1).format(
                {
                    "Billed ($)": "${:.2f}",
                    "Medicare Rate ($)": lambda v: f"${v:.2f}" if pd.notna(v) else "—",
                    "Difference ($)": lambda v: f"${v:+.2f}" if pd.notna(v) else "—",
                    "Difference (%)": lambda v: f"{v:+.1f}%" if pd.notna(v) else "—",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(T("caption_comparison_methodology"))

    # ----------------------------------------------------------------
    # Lettre de contestation
    # ----------------------------------------------------------------
    with st.container(border=True):
        render_section_header("mail", T("section_letter_title"), "✉️")

        disputable_df = df[df["Status"] == "Well above typical — worth disputing"]

        if disputable_df.empty and not elevated_df.empty:
            st.info(T("info_no_elevated_lines"))
        elif disputable_df.empty:
            st.info(T("info_no_disputable_lines"))
        else:
            st.markdown('<span class="mb-cta-marker"></span>', unsafe_allow_html=True)
            if st.button(T("button_generate_letter")):
                if not api_key:
                    st.error(T("err_need_api_key"))
                else:
                    try:
                        client = Anthropic(api_key=api_key)
                        with st.spinner(T("spinner_writing_letter")):
                            letter = generate_dispute_letter(
                                client,
                                sender_name,
                                sender_address,
                                sender_city_state_zip,
                                provider_name,
                                provider_address,
                                patient_name,
                                account_number,
                                bill_date,
                                letter_date,
                                disputable_df,
                            )
                        st.session_state.letter = letter
                        sync_audit_to_store()
                    except Exception as e:
                        st.error(T("err_letter_generation").format(e=e))

            if st.session_state.letter:
                if not st.session_state.payment_confirmed:
                    # ---- Paywall: blurred preview + Paddle pricing cards ----
                    preview_lines = st.session_state.letter.strip().split("\n")
                    teaser = next((l for l in preview_lines if l.strip()), "")
                    st.markdown(
                        f'<p style="font-style: italic; color:#4b5563;">"{teaser} …"</p>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"""
                        <div style="position:relative; border-radius:10px; overflow:hidden;">
                            <div style="filter: blur(5px); user-select:none; pointer-events:none;
                                        background:#f9fafb; padding:1.2rem; border-radius:10px;
                                        max-height:220px; overflow:hidden; white-space:pre-wrap;
                                        font-family: Georgia, serif; font-size:0.9rem; color:#374151;">
    {xml_escape(st.session_state.letter)}
                            </div>
                            <div style="position:absolute; inset:0; display:flex; align-items:center;
                                        justify-content:center; background:rgba(255,255,255,0.35);">
                                <span style="font-weight:700; color:#111827; background:white;
                                             padding:0.5rem 1rem; border-radius:999px;
                                             box-shadow:0 2px 8px rgba(0,0,0,0.15);
                                             display:inline-flex; align-items:center; gap:6px;">
                                    {svg_icon('lock', 16)} {T('lock_label').replace('🔒', '').strip()}
                                </span>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    st.write("")
                    st.markdown(T("unlock_cta"))
                    try:
                        pdf_bytes_for_widget = generate_pdf_letter(st.session_state.letter)
                        pdf_b64_for_widget = base64.b64encode(pdf_bytes_for_widget).decode("utf-8")
                        components.html(
                            render_paddle_pricing_widget(
                                pdf_b64_for_widget, st.session_state.get("app_lang", "en")
                            ),
                            height=650,
                        )
                    except Exception as e:
                        st.error(T("err_pdf_generation").format(e=e))
                    st.caption(T("caption_payment_security"))
                else:
                    # ---- Unlocked: full letter + PDF download ----
                    st.success(T("success_unlocked").format(plan=st.session_state.paid_plan or "standard"))
                    st.text_area(T("label_letter_generated"), st.session_state.letter, height=420)
                    try:
                        pdf_bytes = generate_pdf_letter(st.session_state.letter)
                        st.download_button(
                            T("download_button_pdf"),
                            data=pdf_bytes,
                            file_name="dispute_letter.pdf",
                            mime="application/pdf",
                            type="primary",
                        )
                    except Exception as e:
                        st.error(T("err_pdf_generation").format(e=e))
                        st.download_button(
                            T("download_button_txt_fallback"),
                            data=st.session_state.letter,
                            file_name="dispute_letter.txt",
                            mime="text/plain",
                        )

                    if st.session_state.paid_plan == "pro":
                        st.markdown("---")
                        st.markdown(T("pro_section_title"))
                        if st.button(T("button_generate_followup")):
                            if not api_key:
                                st.error(T("err_need_api_key"))
                            else:
                                try:
                                    client = Anthropic(api_key=api_key)
                                    with st.spinner(T("spinner_writing_letter")):
                                        followup = generate_followup_letter(
                                            client, st.session_state.letter, letter_date
                                        )
                                    st.session_state.followup_letter = followup
                                    sync_audit_to_store()
                                except Exception as e:
                                    st.error(T("err_followup_generation").format(e=e))

                        if st.session_state.get("followup_letter"):
                            st.text_area(
                                T("label_followup_generated"),
                                st.session_state.followup_letter,
                                height=350,
                            )
                            try:
                                followup_pdf = generate_pdf_letter(st.session_state.followup_letter)
                                st.download_button(
                                    T("download_button_followup_pdf"),
                                    data=followup_pdf,
                                    file_name="followup_letter.pdf",
                                    mime="application/pdf",
                                )
                            except Exception as e:
                                st.error(T("err_followup_pdf").format(e=e))

                    # ---- Add-ons purchased alongside the main letter ----
                    if "insurance" in st.session_state.purchased_addons:
                        st.markdown("---")
                        st.markdown(T("addon_insurance_section"))
                        ia1, ia2, ia3 = st.columns(3)
                        with ia1:
                            insurer_name = st.text_input(T("addon_insurer_label"))
                        with ia2:
                            member_id = st.text_input(T("addon_member_id_label"))
                        with ia3:
                            claim_number = st.text_input(T("addon_claim_number_label"))

                        if st.button(T("addon_generate_insurance_btn")):
                            if not api_key:
                                st.error(T("err_need_api_key"))
                            else:
                                try:
                                    client = Anthropic(api_key=api_key)
                                    with st.spinner(T("spinner_writing_letter")):
                                        insurance_letter = generate_insurance_appeal_letter(
                                            client,
                                            sender_name,
                                            sender_address,
                                            sender_city_state_zip,
                                            insurer_name,
                                            member_id,
                                            claim_number,
                                            patient_name,
                                            disputable_df,
                                        )
                                    st.session_state.insurance_appeal_letter = insurance_letter
                                    sync_audit_to_store()
                                except Exception as e:
                                    st.error(T("err_letter_generation").format(e=e))

                        if st.session_state.get("insurance_appeal_letter"):
                            st.text_area(
                                T("addon_insurance_generated_label"),
                                st.session_state.insurance_appeal_letter,
                                height=350,
                            )
                            try:
                                insurance_pdf = generate_pdf_letter(
                                    st.session_state.insurance_appeal_letter
                                )
                                st.download_button(
                                    T("addon_download_insurance_pdf"),
                                    data=insurance_pdf,
                                    file_name="insurance_appeal_letter.pdf",
                                    mime="application/pdf",
                                )
                            except Exception as e:
                                st.error(T("err_pdf_generation").format(e=e))

                    if "phone" in st.session_state.purchased_addons:
                        st.markdown("---")
                        st.markdown(T("addon_phone_section"))
                        if st.button(T("addon_generate_phone_btn")):
                            if not api_key:
                                st.error(T("err_need_api_key"))
                            else:
                                try:
                                    client = Anthropic(api_key=api_key)
                                    with st.spinner(T("spinner_writing_letter")):
                                        phone_script = generate_phone_script(
                                            client, patient_name, account_number, disputable_df
                                        )
                                    st.session_state.phone_script = phone_script
                                    sync_audit_to_store()
                                except Exception as e:
                                    st.error(T("err_letter_generation").format(e=e))

                        if st.session_state.get("phone_script"):
                            st.text_area(
                                T("addon_phone_generated_label"),
                                st.session_state.phone_script,
                                height=300,
                            )
                            try:
                                phone_pdf = generate_pdf_letter(st.session_state.phone_script)
                                st.download_button(
                                    T("addon_download_phone_pdf"),
                                    data=phone_pdf,
                                    file_name="phone_script.pdf",
                                    mime="application/pdf",
                                )
                            except Exception as e:
                                st.error(T("err_pdf_generation").format(e=e))


# --------------------------------------------------------------------------
# FAQ
# --------------------------------------------------------------------------

with st.container(border=True):
    render_section_header("question", T("faq_title"), "❓")
    for i in range(1, 6):
        with st.expander(T(f"faq_q{i}")):
            st.write(T(f"faq_a{i}"))

st.markdown(
    f'<p style="text-align:center; color:#9ca3af; font-size:0.8rem; margin-top:2rem;">'
    f"{T('footer_text')}"
    f"</p>",
    unsafe_allow_html=True,
)
