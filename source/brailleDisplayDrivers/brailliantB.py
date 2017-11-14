#brailleDisplayDrivers/brailliantB.py
#A part of NonVisual Desktop Access (NVDA)
#This file is covered by the GNU General Public License.
#See the file COPYING for more details.
#Copyright (C) 2012-2017 NV Access Limited, Babbage B.V.

import serial
import braille
import inputCore
from logHandler import log
import brailleInput
import bdDetect
import hwIo

TIMEOUT = 0.2
BAUD_RATE = 115200
PARITY = serial.PARITY_EVEN

# Serial
HEADER = "\x1b"
MSG_INIT = "\x00"
MSG_INIT_RESP = "\x01"
MSG_DISPLAY = "\x02"
MSG_KEY_DOWN = "\x05"
MSG_KEY_UP = "\x06"

# HID
HR_CAPS = "\x01"
HR_KEYS = "\x04"
HR_BRAILLE = "\x05"
HR_POWEROFF = "\x07"

KEY_NAMES = {
	# Braille keyboard.
	2: "dot1",
	3: "dot2",
	4: "dot3",
	5: "dot4",
	6: "dot5",
	7: "dot6",
	8: "dot7",
	9: "dot8",
	10: "space",
	# Command keys.
	11: "c1",
	12: "c2",
	13: "c3",
	14: "c4",
	15: "c5",
	16: "c6",
	# Thumb keys.
	17: "up",
	18: "left",
	19: "right",
	20: "down",
}
FIRST_ROUTING_KEY = 80
DOT1_KEY = 2
DOT8_KEY = 9
SPACE_KEY = 10

class BrailleDisplayDriver(braille.BrailleDisplayDriver):
	name = "brailliantB"
	# Translators: The name of a series of braille displays.
	description = _("HumanWare Brailliant BI/B series")
	isThreadSafe = True

	@classmethod
	def getManualPorts(cls):
		return braille.getSerialPorts(filterFunc=lambda info: "bluetoothName" in info)

	def __init__(self, port="auto"):
		super(BrailleDisplayDriver, self).__init__()
		self.numCells = 0

		for portType, portId, port, portInfo in self._getTryPorts(port):
			self.isHid = portType == bdDetect.KEY_HID
			# Try talking to the display.
			try:
				if self.isHid:
					self._dev = hwIo.Hid(port, onReceive=self._hidOnReceive)
				else:
					self._dev = hwIo.Serial(port, baudrate=BAUD_RATE, parity=PARITY, timeout=TIMEOUT, writeTimeout=TIMEOUT, onReceive=self._serOnReceive)
			except EnvironmentError:
				log.debugWarning("", exc_info=True)
				continue
			if self.isHid:
				data = self._dev.getFeature(HR_CAPS)
				self.numCells = ord(data[24])
			else:
				# This will cause the number of cells to be returned.
				self._serSendMessage(MSG_INIT)
				# #5406: With the new USB driver, the first command is ignored after a reconnection.
				# Send the init message again just in case.
				self._serSendMessage(MSG_INIT)
				self._dev.waitForRead(TIMEOUT)
				if not self.numCells:
					# HACK: When connected via bluetooth, the display sometimes reports communication not allowed on the first attempt.
					self._serSendMessage(MSG_INIT)
					self._dev.waitForRead(TIMEOUT)
			if self.numCells:
				# A display responded.
				log.info("Found display with {cells} cells connected via {type} ({port})".format(
					cells=self.numCells, type=portType, port=port))
				break
			self._dev.close()

		else:
			raise RuntimeError("No display found")

		self._keysDown = set()
		self._ignoreKeyReleases = False

	def terminate(self):
		try:
			super(BrailleDisplayDriver, self).terminate()
		finally:
			# Make sure the device gets closed.
			# If it doesn't, we may not be able to re-open it later.
			self._dev.close()

	def _serSendMessage(self, msgId, payload=""):
		if isinstance(payload, (int, bool)):
			payload = chr(payload)
		self._dev.write("{header}{id}{length}{payload}".format(
			header=HEADER, id=msgId,
			length=chr(len(payload)), payload=payload))

	def _serOnReceive(self, data):
		if data != HEADER:
			log.debugWarning("Ignoring byte before header: %r" % data)
			return
		msgId = self._dev.read(1)
		length = ord(self._dev.read(1))
		payload = self._dev.read(length)
		self._serHandleResponse(msgId, payload)

	def _serHandleResponse(self, msgId, payload):
		if msgId == MSG_INIT_RESP:
			if ord(payload[0]) != 0:
				# Communication not allowed.
				log.debugWarning("Display at %r reports communication not allowed" % self._dev.port)
				return
			self.numCells = ord(payload[2])

		elif msgId == MSG_KEY_DOWN:
			payload = ord(payload)
			self._keysDown.add(payload)
			# This begins a new key combination.
			self._ignoreKeyReleases = False

		elif msgId == MSG_KEY_UP:
			payload = ord(payload)
			self._handleKeyRelease()
			self._keysDown.discard(payload)

		else:
			log.debugWarning("Unknown message: id {id!r}, payload {payload!r}".format(id=msgId, payload=payload))

	def _hidOnReceive(self, data):
		rId = data[0]
		if rId == HR_KEYS:
			keys = data[1:].split("\0", 1)[0]
			keys = {ord(key) for key in keys}
			if len(keys) > len(self._keysDown):
				# Press. This begins a new key combination.
				self._ignoreKeyReleases = False
			elif len(keys) < len(self._keysDown):
				self._handleKeyRelease()
			self._keysDown = keys

		elif rId == HR_POWEROFF:
			log.debug("Powering off")
		else:
			log.debugWarning("Unknown report: %r" % data)

	def _handleKeyRelease(self):
		if self._ignoreKeyReleases or not self._keysDown:
			return
		try:
			inputCore.manager.executeGesture(InputGesture(self._keysDown))
		except inputCore.NoInputGestureAction:
			pass
		# Any further releases are just the rest of the keys in the combination being released,
		# so they should be ignored.
		self._ignoreKeyReleases = True

	def display(self, cells):
		# cells will already be padded up to numCells.
		cells = "".join(chr(cell) for cell in cells)
		if self.isHid:
			self._dev.write("{id}"
				"\x01\x00" # Module 1, offset 0
				"{length}{cells}"
			.format(id=HR_BRAILLE, length=chr(self.numCells), cells=cells))
		else:
			self._serSendMessage(MSG_DISPLAY, cells)

	gestureMap = inputCore.GlobalGestureMap({
		"globalCommands.GlobalCommands": {
			"braille_scrollBack": ("br(brailliantB):left",),
			"braille_scrollForward": ("br(brailliantB):right",),
			"braille_previousLine": ("br(brailliantB):up",),
			"braille_nextLine": ("br(brailliantB):down",),
			"braille_routeTo": ("br(brailliantB):routing",),
			"braille_toggleTether": ("br(brailliantB):up+down",),
			"kb:upArrow": ("br(brailliantB):space+dot1",),
			"kb:downArrow": ("br(brailliantB):space+dot4",),
			"kb:leftArrow": ("br(brailliantB):space+dot3",),
			"kb:rightArrow": ("br(brailliantB):space+dot6",),
			"showGui": ("br(brailliantB):c1+c3+c4+c5",),
			"kb:shift+tab": ("br(brailliantB):space+dot1+dot3",),
			"kb:tab": ("br(brailliantB):space+dot4+dot6",),
			"kb:alt": ("br(brailliantB):space+dot1+dot3+dot4",),
			"kb:escape": ("br(brailliantB):space+dot1+dot5",),
			"kb:windows+d": ("br(brailliantB):c1+c4+c5",),
			"kb:windows": ("br(brailliantB):space+dot3+dot4",),
			"kb:alt+tab": ("br(brailliantB):space+dot2+dot3+dot4+dot5",),
			"sayAll": ("br(brailliantB):c1+c2+c3+c4+c5+c6",),
		},
	})

class InputGesture(braille.BrailleDisplayGesture, brailleInput.BrailleInputGesture):

	source = BrailleDisplayDriver.name

	def __init__(self, keys):
		super(InputGesture, self).__init__()
		self.keyCodes = set(keys)

		self.keyNames = names = []
		isBrailleInput = True
		for key in self.keyCodes:
			if isBrailleInput:
				if DOT1_KEY <= key <= DOT8_KEY:
					self.dots |= 1 << (key - DOT1_KEY)
				elif key == SPACE_KEY:
					self.space = True
				else:
					# This is not braille input.
					isBrailleInput = False
					self.dots = 0
					self.space = False
			if key >= FIRST_ROUTING_KEY:
				names.append("routing")
				self.routingIndex = key - FIRST_ROUTING_KEY
			else:
				try:
					names.append(KEY_NAMES[key])
				except KeyError:
					log.debugWarning("Unknown key with id %d" % key)

		self.id = "+".join(names)
