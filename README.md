# render-subtitles
FFmpeg/libass subtitle burn-in worker

## Synchroniser les images avec la narration

L'assembleur lit les `start`/`length` de la première piste image dans
`shotstack_body.timeline.tracks[0].clips` (ou le corps JSON directement).
Sans cible, les durées historiques sont conservées. Chaque image est animée
par FFmpeg à 15 images/s et les segments sont concaténés sans transition
chevauchante ; les fondus existants restent internes à chaque segment.

Pour couvrir la narration réellement générée, fournir une durée mesurée :

```bash
ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 narration.mp3
python3 scripts/assemble.py timeline.json visual.mp4 --target-duration 69.85
```

Le workflow **Assemble visual video (FFmpeg)** expose le paramètre facultatif
`target_duration` (chaîne décimale en secondes). GitHub ne reçoit pas le fichier
audio dans ce workflow : n8n devra mesurer la durée du fichier de narration
**après sa génération**, puis transmettre cette valeur, par exemple `"69.85"`.
La durée calculée à partir du texte ou d'une estimation TTS ne suffit pas.
Aucun workflow n8n n'est modifié par ce dépôt.

La cible est la durée **totale**, fondu final inclus. Ne pas ajouter une seconde
pour le fondu : pour une narration de 69,85 s, transmettre `target_duration=69.85`
à l'assemblage puis `duration=69.85` et `audio_url` au workflow de rendu des
sous-titres. Le fondu final couvre alors 68,85–69,85 s et n'allonge pas le montage.
Le script de rendu et les modifications audio/sous-titres sont conservés.
Les secrets et chemins Supabase restent inchangés.

Les durées visuelles sont réparties proportionnellement sur des frontières
entières d'images, sans trous ni chevauchements. Le total est arrondi vers le
haut à 15 images/s : 64,33 s réajustées à 69,85 s donnent **1 048 images**, soit
**69,8667 s**. La marge est inférieure à une image (66,7 ms), au lieu de plusieurs
secondes de dernière image figée. Zooms, panoramiques et fondus sont recalculés
sur toute la durée de chaque segment ; aucune image n'est ajoutée par `tpad`
dans l'assembleur. Les propriétés des images et le format 1080 × 1920 restent
identiques. La narration n'est ni réencodée ni accélérée par l'assembleur silencieux.

Une cible supérieure à **70 secondes** est refusée avant téléchargement/rendu.
Il faut alors raccourcir le texte en amont et régénérer la narration ; ne pas
couper arbitrairement le fichier ni modifier sa vitesse. Si la narration est
inférieure à 60 s, enrichir le contenu en amont si la plage 60–70 s est requise.
Sans cible explicite, une timeline supérieure à 70 s produit un avertissement
et conserve le comportement historique. Le plafond de 50 Mo dépend du contenu :
le contrôler après le rendu final, sans changer la compression préventivement.

## Tests locaux (sans production)

```bash
python3 -m unittest discover -s tests -v
```

Les tests couvrent le cas 64,33 → 69,85 s, la proportionnalité, les frontières
continues, les erreurs de durée et la limite de 70 s. Avec FFmpeg/ffprobe installés,
un test d'intégration génère une image synthétique, rend les cinq effets en
1080 × 1920, vérifie 1 048 images et contrôle que les six dernières secondes restent animées, sans gel de plus
de trois images (0,2 s ; un panoramique peut répéter brièvement un pixel). Les téléchargements sont remplacés par une
fixture locale ; aucun upload, secret, publication ou service de production
n'est utilisé. Sans FFmpeg, le test d'intégration est explicitement ignoré.
