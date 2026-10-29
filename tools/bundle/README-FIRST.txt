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

2. The first time only, fill in the page:
     Device token:         copy the line in DEVICE-TOKEN.txt
     Registration number:  10000001AA  (others: 10000002AA, 10000003AA)
     Exam code:            UTME-SAMPLE
   The page remembers these on this PC. From then on the Start button is selected when
   the page opens, so the tester only presses Enter.
   Each new copy of this folder has its own token. If the page says the token was not
   accepted, paste the line from this folder's DEVICE-TOKEN.txt and press Start again.

3. The first time, the browser asks to use the microphone: allow it.

To change the voice, close the black window and double-click another "Start exam" file.
To run the exam again from the beginning, close the black window and double-click
"Start over (delete test sessions)". Or sign in with another registration number.

If the page was closed by mistake, double-click "Open the exam page again". The exam
carries on where it was.


For the tester
--------------

The service reads everything aloud. A screen reader can stay on: the page tells it not
to read the exam a second time.

  Hold Space to talk. Release it when you have finished speaking.
  Pressing Space also stops the voice at once, so you can interrupt it.

You can say things like: "start", "repeat", "next", "go to question 12", "B",
"I'll go with the third one", "yes", "no", "how much time is left", "which questions
have I not answered", "slower", "faster", "help".

Keys work too, without speaking:
  1 to 5    choose option A to E
  Enter     yes (confirm)
  Esc       no (cancel)
  R         repeat the question
  N         next question
  P         previous question

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

To measure how fast this PC is: close the black window, double-click "Measure this PC"
(about five minutes; don't use the PC meanwhile), then "Collect feedback" again.

The device token in this folder is for testing only.
