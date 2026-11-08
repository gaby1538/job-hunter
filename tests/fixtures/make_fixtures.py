"""Generate the sample alert emails used by the tests.

The layouts mirror real alerts received in Nov 2026 (Indeed, LinkedIn, HelloWork,
Free-Work, Malt). Personal data is replaced: recipient address, tracking tokens,
names, and the private Malt client. eFinancialCareers is still synthetic (no real
alert available yet).

Run:  python tests/fixtures/make_fixtures.py
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from email.policy import default
from pathlib import Path

OUT = Path(__file__).parent / "emails"
ME = "me@example.com"


def save(name: str, sender: str, subject: str, date: str, text: str | None = None,
         html: str | None = None) -> None:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ME
    msg["Subject"] = subject
    msg["Date"] = date
    msg["Message-ID"] = f"<{name}@example.com>"
    if text is not None:
        msg.set_content(text, cte="8bit")
    if html is not None:
        if text is None:
            msg.set_content(html, subtype="html", cte="8bit")
        else:
            msg.add_alternative(html, subtype="html", cte="8bit")
    (OUT / f"{name}.eml").write_bytes(msg.as_bytes(policy=default))


# ----------------------------------------------------------------- Indeed (text part used)
INDEED_TEXT = """Indeed Job Alert
2 nouveaux emplois quantitative analyst - Paris (75)

Jobs 1-2 of 2 new jobs
See matching results on Indeed: https://fr.indeed.com/jobs?q=quantitative+analyst&from=ja&alid=TOKEN&utm_campaign=job_alerts

Analyste quantitatif (F/H) - Risques de crédit
CONFEDERATION NATIONALE DU CREDIT MUTUEL - Paris (75)
De 60 000 € à 65 000 € par an
Rejoindre le Crédit Mutuel, c'est rejoindre une banque coopérative.
il y a 10 jours
https://fr.indeed.com/pagead/clk/dl?mo=r&ad=SPONSOREDAD&tmtk=TOKEN&alid=TOKEN&fvj=1

Stage - 6 mois - Analyste Investissement F/H
Natixis - Paris (75)
Vous assurerez l'interface avec notre équipe informatique interne.
il y a 1 jour
https://fr.indeed.com/rc/clk/dl?jk=fd580c1a50dfdc62&from=ja&qd=TOKEN&rd=TOKEN&tk=TOKEN&alid=TOKEN&bb=TOKEN

Ne partagez pas cet email
Si le salaire n'est pas mentionné dans une offre d'emploi, il sera estimé.
Gérer les alertes Emploi: https://subscriptions.indeed.com?token=TOKEN
Se désabonner de cette alerte Emploi: https://subscriptions.indeed.com/alerts/cancel?token=TOKEN
"""
INDEED_HTML = "<html><body><p>HTML version - the text part is used for Indeed.</p></body></html>"

# ----------------------------------------------------------------- LinkedIn (text part)
LI_TRACK = "?trackingId=TOKEN&refId=TOKEN&lipi=TOKEN&midToken=TOKEN&trk=eml-job&otpToken=TOKEN"
LINKEDIN_TEXT = f"""Votre alerte Emploi pour Risk (Paris)
CONSULTANTS RISQUE DE CREDIT
Wise Associates
Paris

Cette entreprise recrute activement
Voir l’offre d’emploi : https://www.linkedin.com/comm/jobs/view/4486648624/{LI_TRACK}

---------------------------------------------------------

Consultant MOA Finance | Asset Management Expert
OMICRONE
Paris

Cette entreprise recrute activement
Postulez avec un CV et un profil
Voir l’offre d’emploi : https://www.linkedin.com/comm/jobs/view/4486653622/{LI_TRACK}

---------------------------------------------------------

Voir toutes les offres d’emploi sur LinkedIn : https://www.linkedin.com/comm/jobs/search-results/?keywords=Risk{LI_TRACK}
Gérez vos alertes Emploi : https://www.linkedin.com/comm/jobs/alerts{LI_TRACK}
Se désabonner : https://www.linkedin.com/job-alert-email-unsubscribe?savedSearchId=1
"""

# ----------------------------------------------------------------- HelloWork (HTML, redirects)
def hw_link(n: int, job_id: int, content: str, path: str = "") -> str:
    page = path or f"emplois/{job_id}.html"
    target = (f"{ME}\U0001faa2https://www.hellowork.com/fr-fr/{page}"
              f"?utm_source=pushOffres&utm_medium=email&utm_content={content}")
    payload = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    return f"https://emails.hellowork.com/clic/00000000-0000-0000-0000-000000000000/{n}/abcdef/{payload}"


def hw_card(n: int, job_id: int, title: str, company: str, location: str, contract: str,
            salary: str | None = None, badge: bool = False) -> str:
    badge_cell = "<td>Super recruteur</td>" if badge else ""
    salary_row = f'<table><tr><td>{salary} <a href="#"></a></td></tr></table>' if salary else ""
    return f"""
<table><tr><td>{title} <a href="{hw_link(n, job_id, 'liste1title')}"><img src="arrow.png" alt=""></a></td></tr></table>
<table><tr><td>{company}</td>{badge_cell}</tr></table>
<table><tr><td>{location}<a href="#"></a></td></tr></table>
<table><tr><td>{contract}<a href="#"></a></td></tr></table>
{salary_row}
<table><tr><td>Voir l’offre<a href="{hw_link(n + 1, job_id, 'liste1CTA')}"><img src="btn.png" alt=""></a></td></tr></table>
"""


HELLOWORK_HTML = f"""<html><head><style>td {{ font-family: Arial; }}</style></head><body>
<table><tr><td>60 000 - 75 000 € / an pour ce poste, et 3 autres offres correspondent à votre profil</td></tr></table>
<table><tr><td><a href="{hw_link(1, 0, 'logo', path='index.html')}"><img src="logo.png" alt=""></a></td></tr></table>
<table><tr><td>Hello ! </td></tr><tr><td>4 nouvelles offres</td></tr><tr><td>correspondent à votre profil.</td></tr></table>
{hw_card(3, 85099350, "Ingénieur Intelligence Artificielle H/F", "Amiltone", "Marseille - 13", "CDI")}
{hw_card(6, 85286442, "Chef de Projet Dematerialisation Si H/F", "Prospectis RH", "Paris - 75", "CDI", "60 000 - 75 000 € / an", badge=True)}
{hw_card(9, 85115597, "Chef de Projet Informatique H/F", "Experis France", "Marseille 2e - 13", "Freelance", "45 000 - 55 000 € / an")}
<table><tr><td>Voir toutes les offres<a href="{hw_link(12, 0, 'all', path='candidat/mon-espace/offres.html')}"><img src="all.png" alt=""></a></td></tr></table>
<table><tr><td>Quand on a vu ces offres,</td></tr><tr><td>on a pensé à vous.</td></tr></table>
{hw_card(13, 84971991, "Chargée - Chargé de Mission H/F", "Crédit Mutuel", "Paris 17e - 75", "CDI", "43 000 - 45 000 € / an")}
<table><tr><td>À très vite dans vos recherches ! L'équipe Hellowork</td></tr></table>
<table><tr><td>Vous pouvez modifier vos préférences <a href="{hw_link(20, 0, 'prefs', path='candidat/parametres/abonnements.html')}">ici</a> ou vous désabonner<a href="{hw_link(21, 0, 'desabo', path='r/1/desabonnement/push')}"></a>.</td></tr></table>
</body></html>"""

# ----------------------------------------------------------------- Free-Work (text part)
FREEWORK_TEXT = """Voir la version en ligne https://example.mjt.lu/nl3/TOKEN

<<freework logo>> [https://www.free-work.com/fr/tech-it]

Dernières offres de mission et d'emploi [https://www.free-work.com/fr/tech-it/jobs?contracts=contractor]

Freelance
Chef de projet Power BI
💶 400 - 650 €⁄j

 📍Paris
<<Postuler>> [https://www.free-work.com/fr/tech-it/job-mission/consultant-decisionnel-bi-powerbi-sas-tableau/chef-de-projet-powerbi-1]

Freelance
Quant Risk Analyst - XVA
💶 650 - 800 €⁄j

 📍Paris
<<Postuler>> [https://www.free-work.com/fr/tech-it/job-mission/analyste-risque/quant-risk-analyst-xva]

Accédez aux opportunités qui vous correspondent.
<<Lancer le matching d'offres>> [https://www.free-work.com/fr/tech-it/jobs/suggested]

--------------------------------------------------------------------------------

Le marché repart pour les développeurs, pas pour les codeurs
<< Lire l'article>> [https://www.free-work.com/fr/tech-it/blog/webinaires-et-livres-blancs/le-marche-repart]

Cet email a été envoyé à me@example.com, cliquez ici pour vous désabonner https://example.mjt.lu/unsub2?m=TOKEN
"""

# ----------------------------------------------------------------- Malt (HTML, one offer)
MALT_HTML = """<html><head><style>.heading { font-size: 32px; }</style></head><body>
<p class="heading">Bonne nouvelle,<br/>cette opportunité correspond à votre profil !</p>
<table class="main-table"><tbody><tr><td align="center" class="section">
  <img class="company-frame" src="https://attachements.malt.com/company-placeholder-22.png">
  <p class="section-name">CLIENT EXEMPLE</p>
  <p class="opportunity-name">Consultant Risque de Marché </p>
</td></tr>
<tr><td><div class="details-table"><div class="section">
  <p class="section-name">Description</p>
  <p class="section-details"><strong>&Agrave; propos du projet</strong> Notre client, une banque, renforce son &eacute;quipe risques de march&eacute; (VaR, Expected Shortfall).</p>
</div></div></td></tr></tbody></table>
<table class="buttons-section"><tbody><tr>
  <td><!--[if gte mso 9]><table><tr><th><![endif]-->
  <a href="https://www.malt.fr/messages/client-project-offer/6ad09891403102c7db82a9b8?utm_source&#x3D;mandrill&amp;utm_medium&#x3D;email" title="Répondre au client"><span>Répondre au client</span></a></td>
  <td><a href="https://www.malt.fr/client/sourcing-projects/freelancer-project-decline/6ad09891403102c7db82a9b8?token&#x3D;TOKEN" title="Décliner"><span>Décliner</span></a></td>
</tr></tbody></table>
<table class="main-table"><tbody>
<tr><td class="section col2"><p class="section-name">Nombre de jours de travail</p><p class="section-details">Toute la semaine</p></td>
    <td class="section col2"><p class="section-name">Début</p><p class="section-details">Dès que possible</p></td></tr>
<tr><td class="section col2"><p class="section-name">Télétravail</p><p class="section-details">Oui (2 jours/semaine)</p></td>
    <td class="section col2"><p class="section-name">Durée</p><p class="section-details">6 mois</p></td></tr>
<tr><td class="section col2"><p class="section-name">Lieu</p><p class="section-details">Paris, France</p></td>
    <td class="section col2"><p class="section-name">Tarif journalier initial</p><p class="section-details">Selon profil</p></td></tr>
</tbody></table>
</body></html>"""

# ----------------------------------------------------------------- not alerts
INMAIL_TEXT = "Bonjour, je vous contacte au sujet d'un poste. https://www.linkedin.com/comm/jobs/view/123/"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    save("indeed_alert", "Indeed <donotreply@jobalert.indeed.com>",
         "Analyste quantitatif (F/H) - Risques de crédit à CONFEDERATION NATIONALE DU CREDIT MUTUEL",
         "Sun, 08 Nov 2026 16:45:21 +0000", text=INDEED_TEXT, html=INDEED_HTML)
    save("linkedin_alert", "Alertes LinkedIn Jobs <jobalerts-noreply@linkedin.com>",
         "CONSULTANTS RISQUE DE CREDIT chez Wise Associates",
         "Sun, 08 Nov 2026 13:53:08 +0000", text=LINKEDIN_TEXT)
    save("linkedin_inmail", "Recruiter via LinkedIn <inmail-hit-reply@linkedin.com>",
         "Opportunité", "Thu, 05 Nov 2026 17:52:08 +0000", text=INMAIL_TEXT)
    save("hellowork_alert", "Hellowork notifications <notification@emails.hellowork.com>",
         "Chef de Projet Dematerialisation Si à Paris - 75",
         "Sun, 08 Nov 2026 09:39:49 +0000", html=HELLOWORK_HTML)
    save("freework_newsletter", "Free-Work <contact@free-work.com>",
         "Dernières missions sur Free-Work !", "Thu, 05 Nov 2026 06:37:01 +0000",
         text=FREEWORK_TEXT)
    save("malt_opportunity", '"Malt [no reply]" <noreply@malt.com>',
         "Nouvelle opportunité avec CLIENT EXEMPLE", "Tue, 27 Oct 2026 07:13:59 +0000",
         html=MALT_HTML)
    save("malt_webinar", "Malt Community <community-fr@malt.com>",
         "Webinaire Premiers pas sur Malt", "Tue, 03 Nov 2026 09:30:30 +0000",
         html="<html><body><a href='https://www.malt.fr/webinar'>S'inscrire</a></body></html>")


if __name__ == "__main__":
    main()
