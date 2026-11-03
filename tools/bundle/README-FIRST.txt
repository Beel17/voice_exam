Voice exam: test bundle
=======================

This folder runs a practice voice exam on this PC. Nothing to install, and no internet
needed. Windows 10 or 11, 64-bit, with speakers or headphones and a microphone (a
headset is best).

The exam is fictional practice material, not a real exam.


For the person helping the tester
---------------------------------

1. Double-click one of the three "Start exam" files. Each uses a different voice:
     Start exam - voice 1 (American woman)
     Start exam - voice 2 (British man)
     Start exam - voice 3 (British woman)
   A black window opens. Leave it open: closing it stops the exam service.
   After about 15 seconds (up to a minute on a slow PC), the exam page opens in the
   web browser by itself.

2. The first time only, the page asks for the device token: copy the line in
   DEVICE-TOKEN.txt and press Save. The page remembers it on this PC. Each new copy of
   this folder has its own token; if the page says the token was not accepted, paste the
   line from this folder's DEVICE-TOKEN.txt again.

3. Choose the mode for the tester:
     Standard mode, for testers who read: the page opens in it. The tester signs in
       with an examination number, checks their name, and answers by clicking.
     Audio mode, for blind or visually impaired testers: on the sign-in page, click
       "Audio mode (for candidates who listen)". The page remembers it. The tester
       presses Space to begin; the service then speaks everything.
   The first time in audio mode, the browser asks to use the microphone: allow it.

   Practice examination numbers (fictional candidates):
     20260001PR to 20260006PR   standard mode
     20260007PR, 20260008PR     registered for audio mode
   In audio mode the tester says the number one character at a time, for example
   "two zero two six zero zero zero seven, P for Papa, R for Romeo", hears it read back,
   and says "yes". You can also type it in the box at the bottom of the page.

To change the voice, close the black window and double-click another "Start exam" file.
To run the exam again from the beginning, close the black window and double-click
"Start over (delete test sessions)". Or sign in with another examination number.

If the page was closed by mistake, double-click "Open the exam page again". The exam
carries on where it was. When a tester has finished, click "Done" on the receipt page:
the page is then ready for the next tester.

Optional: run it as a Windows service, as an exam centre would. Double-click
"Install as a Windows service" and allow the Windows prompt (it needs administrator
rights). The service then starts by itself with the PC, with no black window, and
restarts itself if it ever stops; open the exam page with "Open the exam page again".
It uses voice 1. To go back to the black window, run "Remove the Windows service".

"Show the service status" opens a page, for whoever looks after the exam, saying in
plain words whether everything is working. Above all it shows any answers still waiting
to reach the exam store: they are kept safely on this PC and written automatically while
the service runs. Never delete the folder, or run "Start over", while any are waiting.

The "Exam store" and "Sync" files are the exam centre's tasks. Testers do not need
them: the practice paper is already loaded and open.
  Exam store - list papers                 what is loaded, and which papers are open
  Exam store - import a paper              load an exam package (.zip)
  Exam store - open a paper for sign-in    let candidates sign in to a paper
  Exam store - close a paper               stop candidates signing in to it
  Sync - download papers                   fetch this centre's papers from the cloud
  Sync - upload results                    send every answer and submission to the cloud
  Sync - status                            results waiting, and the last download and upload
Sync needs the cloud's address and centre ID in config\centre.toml ([sync]) and the
centre's token in data\sync-token.txt.


For the tester
--------------

Standard mode: click an option to answer; it is saved at once. Previous and Next, or the
numbered buttons, move between questions; Revisit Later marks a question to come back
to. Keys: A to E answer, the arrow keys move. Submit Exam shows a summary, then asks once
more before submitting.

Audio mode: the service reads everything aloud. A screen reader can stay on.

  Hold Space to talk. Release it when you have finished speaking.
  Pressing Space also stops the voice at once, so you can interrupt it.

You can say things like: "start", "repeat", "next", "go to question 12", "B",
"I'll go with the third one", "yes", "no", "how much time is left", "which questions
have I not answered", "flag this", "slower", "faster", "help".

Keys work too, without speaking:
  1 to 5    choose option A to E
  Enter     yes (confirm)
  Esc       no (cancel)
  R         repeat the question
  N         next question
  P         previous question
  F         flag the question to revisit
  T         time left
  S         submit (asked twice)

An answer is only saved after you confirm it. The service says "Saved" the first few
times, then just plays a short sound.


After testing
-------------

Double-click "Collect feedback". It makes a folder called feedback with:
  - a transcript of each test session: what the service heard, what it understood, and
    what it said back. It records the words the tester spoke, as text. No audio is
    kept.
  - FEEDBACK-FORM.txt, to fill in with the tester.
Send the whole feedback folder back. Tell testers beforehand that their spoken words are
kept as text in these transcripts, and only send them if they agree.

If the service window says "language model unavailable", close it, double-click
"Diagnose the language model" (a few minutes), then "Collect feedback": the feedback folder
then holds model-diagnosis.txt, which says why.

To measure how fast this PC is: close the black window, double-click "Measure this PC"
(about five minutes; don't use the PC meanwhile), then "Collect feedback" again.

The device token in this folder is for testing only.
