from pronunciation.recognize import warm, recognize
warm()
data = open("fix_test.wav", "rb").read()   # last night's file, or any .wav you have
print(repr(recognize(data)))