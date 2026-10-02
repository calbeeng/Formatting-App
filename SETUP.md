# One-time Google setup

The app needs permission to write to your Google Docs. Google only gives that
permission to apps registered in **Google Cloud**, so you'll register your own
private copy. It's free, takes about 15 minutes, and you only do it once. Your
girlfriend can then use the same registration (see step 7).

You'll end up with one small file, the **client file**, which you give to the
app the first time you sign in.

> Google sometimes renames buttons. If something doesn't match exactly, look
> for the closest wording. These steps were checked against Google's own
> instructions in October 2026.

---

## 1. Create a Google Cloud project

1. Go to **https://console.cloud.google.com** and sign in with your Gmail account.
   Accept the terms if asked.
2. At the top left, click the **project picker** (it may say "Select a project").
3. Click **New project**.
4. Name it something like `notes2gdoc` and click **Create**.
5. Wait a few seconds, then make sure the new project is selected in the
   project picker.

## 2. Turn on the Google Docs and Google Drive APIs

1. In the search bar at the top, type **Google Docs API**, open it, and click **Enable**.
2. Search for **Google Drive API**, open it, and click **Enable**.
   (Drive is only used to briefly upload flowchart pictures. The app can only
   ever see files it created itself.)

## 3. Set up the sign-in ("consent") screen

1. Open the menu (☰) → **Google Auth platform** → **Branding**.
   If you see **Get started**, click it.
2. **App name:** `notes2gdoc`. **User support email:** your Gmail. Click **Next**.
3. **Audience:** choose **External**. Click **Next**.
4. **Contact information:** your Gmail. Click **Next**.
5. Tick **I agree to the Google API Services: User Data Policy**, then click
   **Continue** and **Create**.

## 4. Tell Google which permissions the app uses

1. In **Google Auth platform**, open **Data Access**.
2. Click **Add or remove scopes**.
3. In the box for adding scopes manually, paste these lines:
   ```
   https://www.googleapis.com/auth/documents
   https://www.googleapis.com/auth/drive.file
   openid
   https://www.googleapis.com/auth/userinfo.email
   ```
   Click **Add to table**, make sure they're ticked, then click **Update**.
4. Click **Save**.

## 5. Add you and your girlfriend as test users

1. In **Google Auth platform**, open **Audience**.
2. Under **Test users**, click **Add users**.
3. Enter **both** Gmail addresses (yours and hers), then click **Save**.

## 6. Create the client file

1. In **Google Auth platform**, open **Clients**.
2. Click **Create client**.
3. **Application type:** **Desktop app**. **Name:** `notes2gdoc desktop`.
4. Click **Create**.
5. In the box that appears, click **Download JSON**. The file is called
   something like `client_secret_1234-abc.apps.googleusercontent.com.json`
   and lands in your **Downloads** folder.
   - Download it right away. If you close the box first, open the client from
     the **Clients** list and use its download button.
   - Keep this file private: don't post it online. It's fine to send it
     directly to your girlfriend.

## 7. First sign-in in the app

1. Open the app (the **Notes to Google Docs** icon).
2. Click **Sign in with Google** at the top right.
3. The first time, the app asks for the client file. Click **OK** and choose
   the JSON file from step 6. The app copies it into its own settings folder,
   so you can delete it from Downloads afterwards (keep a copy somewhere for
   your girlfriend):
   - Windows: `%APPDATA%\notes2gdoc\`
   - Mac: `~/Library/Application Support/notes2gdoc/`
4. Your web browser opens Google's sign-in page. Choose your account.
5. You'll see **"Google hasn't verified this app"**. That's expected: it's
   your own app and Google hasn't reviewed it. Click **Continue** (or
   **Advanced → Go to notes2gdoc (unsafe)**, depending on the screen).
6. On the permissions screen, **tick every box** (Google Docs, and the Drive
   files this app creates), then click **Continue**.
7. The browser says "Signed in". Go back to the app. It now shows
   **Signed in as you@gmail.com**.

**Your girlfriend:** she uses the same client file you downloaded. When she
first clicks **Sign in with Google** on her computer, she chooses that file
and signs in with *her* Gmail. Each of you has a separate sign-in saved on
your own computer.

---

## The 7-day sign-in limit, and how to avoid it

While your Google Cloud app is in **Testing** mode (the default), Google lets
each sign-in last only **7 days**. After that, the app says your sign-in has
expired and you click **Sign in with Google** again. Nothing breaks, it's just
a bit annoying.

You have two options:

**Option A: stay in Testing (simplest, and fine for now).** Sign in again
roughly once a week. Only the test users from step 5 can sign in.

**Option B: publish the app for personal use.**
1. First, Google requires extra details on the **Branding** page: an
   **App home page** link, a **Privacy policy** link, and the **Authorized
   domain** they're on. Until those are filled in, **Publish app** stays greyed
   out ("To publish your app, you must complete your configuration on the
   Branding page"). A free GitHub Pages site (`yourname.github.io`) can host
   both pages; this will be set up alongside the downloadable builds in Phase 4.
2. Then go to **Google Auth platform** → **Audience** → under **Publishing status**,
   click **Publish app**, then **Confirm**. The status becomes **In production**.
3. You don't need to submit it for Google's review. Unreviewed apps are
   allowed for personal use by fewer than 100 people.
4. What changes:
   - Sign-ins no longer expire after 7 days. They last until you sign out,
     remove access, or don't use the app for about 6 months.
   - The sign-in screen still shows **"Google hasn't verified this app"**.
     Click **Advanced** → **Go to notes2gdoc (unsafe)**. "Unsafe" just means
     "not reviewed by Google". It's your own app.
   - The test-user list no longer applies: anyone who has your client file
     could sign in. Another reason to keep that file private.

Sources: Google's documentation on
[refresh token expiration](https://developers.google.com/identity/protocols/oauth2#expiration),
[unverified apps](https://support.google.com/cloud/answer/7454865) and
[verification exceptions for personal use](https://support.google.com/cloud/answer/13464323).

---

## Good to know

- **What the app can access:** only Google Docs you point it at (Google's
  permission covers your Docs in general, but the app only opens the link you
  paste), and Drive files the app itself creates.
- **Flowchart pictures:** Google Docs can only insert a picture from a web
  link. So for each diagram the app uploads the picture to your Drive, makes it
  viewable by link (a long, random link), inserts it into your doc, and
  deletes the upload straight away. The doc keeps its own copy.
- **Removing access at any time:** go to
  https://myaccount.google.com/permissions → **notes2gdoc** → **Remove access**.
  You can also click **Sign out** in the app.
- **Starting over:** delete the `notes2gdoc` folder listed in step 7. The app
  will ask for the client file and sign-in again.
