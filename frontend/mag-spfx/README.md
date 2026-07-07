# מערכת ניתוח תיקי חקירה - ממשק SharePoint

זהו ממשק SPFx עבור העלאת מסמכים, מעקב אחר עיבוד, הצגת גרף ידע, צפייה בדו"ח ושיחה עם סוכן חקירה.

## טכנולוגיות

- SharePoint Framework 1.23
- React 17
- TypeScript
- Fluent UI
- Cytoscape.js להצגת גרף ידע

## הרצה מקומית

```bash
npm install
npm run trust-cert
npm run serve
```

שרת הפיתוח חושף את קובץ המניפסט בכתובת:

```text
https://localhost:4321/temp/build/manifests.js
```

ב־SPFx 1.23 אין דף workbench מקומי ישן. יש לפתוח workbench מתוך SharePoint:

```text
https://<tenant>.sharepoint.com/_layouts/15/workbench.aspx?debugManifestsFile=https%3A%2F%2Flocalhost%3A4321%2Ftemp%2Fbuild%2Fmanifests.js&loadSPFX=true
```

## הגדרות Web Part

- `apiBaseUrl`: כתובת שירות הענן.
- `functionKey`: מפתח פונקציה לבדיקות פיתוח. הערך גלוי בכלי הדפדפן ואינו מודל הזדהות לייצור.
- `useMockData`: שימוש בנתוני דמה במקום קריאות חיות.
- `pollIntervalMs`: מרווח בדיקת מצב במילישניות.

## חוזי Backend

זמינים כעת:

- `POST /api/pipelines`: מקבל גוף PDF ומתחיל עיבוד.
- `GET /api/pipelines/{instanceId}`: מחזיר מצב עיבוד.
- `POST /api/query`: מקבל `{ "question": "...", "doc_id": "..." }` ומתחיל שאילתת סוכן.
- כתובת Durable status המוחזרת מקריאת השאילתה.

נקודות קריאה עבור הממשק:

- `GET /api/cases/{docId}/graph`: מחזירה גרף להצגה מתוך Gremlin לפי `partitionKey = docId`.
- `GET /api/cases/{docId}/report`: מחזירה את `GRAPH_REPORT.md` מתוך Blob Storage.

עמוד הגרף אינו קורא קובצי JSON זמניים. נתוני ההדגמה נשמרים בזיכרון בלבד מאחורי אותו מתאם שישמש את נקודת ה־Gremlin.
