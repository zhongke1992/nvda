#A part of NonVisual Desktop Access (NVDA)
#Copyright (C) 2016 NV Access Limited
#This file is covered by the GNU General Public License.
#See the file COPYING for more details.

import time
import appModuleHandler
import speech
import sayAllHandler
import eventHandler
import api
from scriptHandler import willSayAllResume, isScriptWaiting
import controlTypes
import treeInterceptorHandler
from cursorManager import ReviewCursorManager
import browseMode
from browseMode import BrowseModeDocumentTreeInterceptor
import textInfos
from textInfos import DocumentWithPageTurns
from NVDAObjects.IAccessible import IAccessible
from globalCommands import SCRCAT_SYSTEMCARET
from NVDAObjects.IAccessible.ia2TextMozilla import MozillaCompoundTextInfo
import IAccessibleHandler
import aria
import winUser
from logHandler import log
import ui

class BookPageViewTreeInterceptor(DocumentWithPageTurns,ReviewCursorManager,BrowseModeDocumentTreeInterceptor):

	TextInfo=treeInterceptorHandler.RootProxyTextInfo
	pageChangeAlreadyHandled = False

	def turnPage(self,previous=False):
		# When in a page turn, Kindle  fires focus on the new page in the table of contents treeview.
		# We must ignore this focus event as it is a hinderance to a screen reader user while reading the book.
		try:
			self.rootNVDAObject.appModule.inPageTurn=True
			self.rootNVDAObject.turnPage(previous=previous)
			# turnPage waits for a pageChange event before returning,
			# but the pageChange event will still get fired.
			# We need to know that we've already handled it.
			self.pageChangeAlreadyHandled=True
		finally:
			self.rootNVDAObject.appModule.inPageTurn=False

	def event_pageChange(self, obj, nextHandler):
		if self.pageChangeAlreadyHandled:
			# This page change has already been handled.
			self.pageChangeAlreadyHandled = False
			return
		info = self.makeTextInfo(textInfos.POSITION_FIRST)
		self.selection = info
		info.expand(textInfos.UNIT_LINE)
		speech.speakTextInfo(info, unit=textInfos.UNIT_LINE, reason=controlTypes.REASON_CARET)

	def isAlive(self):
		return winUser.isWindow(self.rootNVDAObject.windowHandle)

	def __contains__(self,obj):
		return obj==self.rootNVDAObject

	def _changePageScriptHelper(self,gesture,previous=False):
		if isScriptWaiting():
			return
		try:
			self.turnPage(previous=previous)
		except RuntimeError:
			return
		info=self.makeTextInfo(textInfos.POSITION_FIRST)
		self.selection=info
		info.expand(textInfos.UNIT_LINE)
		if not willSayAllResume(gesture): speech.speakTextInfo(info,unit=textInfos.UNIT_LINE,reason=controlTypes.REASON_CARET)

	def script_moveByPage_forward(self,gesture):
		self._changePageScriptHelper(gesture)
	script_moveByPage_forward.resumeSayAllMode=sayAllHandler.CURSOR_CARET

	def script_moveByPage_back(self,gesture):
		self._changePageScriptHelper(gesture,previous=True)
	script_moveByPage_back.resumeSayAllMode=sayAllHandler.CURSOR_CARET

	def _tabOverride(self,direction):
		return False

	def script_showSelectionOptions(self, gesture):
		fakeSel = self.selection
		if fakeSel.isCollapsed:
			# Double click to access the toolbar; e.g. for annotations.
			try:
				p = fakeSel.pointAtStart
			except NotImplementedError:
				log.debugWarning("Couldn't get point to click")
				return
			log.debug("Double clicking")
			winUser.setCursorPos(p.x, p.y)
			winUser.mouse_event(winUser.MOUSEEVENTF_LEFTDOWN, 0, 0, None, None)
			winUser.mouse_event(winUser.MOUSEEVENTF_LEFTUP, 0, 0, None, None)
			winUser.mouse_event(winUser.MOUSEEVENTF_LEFTDOWN, 0, 0, None, None)
			winUser.mouse_event(winUser.MOUSEEVENTF_LEFTUP, 0, 0, None, None)
			return

		# The user makes a selection using browse mode virtual selection.
		# Update the selection in Kindle.
		# This will cause the options to appear.
		fakeSel.innerTextInfo.updateSelection()
		# The selection might have been adjusted to meet word boundaries,
		# so retrieve and report the selection from Kindle.
		# we can't just use self.makeTextInfo, as that will use our fake selection.
		realSel = self.rootNVDAObject.makeTextInfo(textInfos.POSITION_SELECTION)
		# Translators: Announces selected text. %s is replaced with the text.
		speech.speakSelectionMessage(_("selected %s"), realSel.text)
		# Remove our virtual selection and move the caret to the active end.
		fakeSel.innerTextInfo = realSel
		fakeSel.collapse(end=not self._lastSelectionMovedStart)
		self.selection = fakeSel
	# Translators: Describes a command.
	script_showSelectionOptions.__doc__ = _("Shows options related to selected text or text at the cursor")
	script_showSelectionOptions.category = SCRCAT_SYSTEMCARET

	__gestures = {
		"kb:control+c": "showSelectionOptions",
		"kb:applications": "showSelectionOptions",
		"kb:shift+f10": "showSelectionOptions",
	}

	def _iterEmbeddedObjs(self, hypertext, startIndex, direction):
		"""Recursively iterate through all embedded objects in a given direction starting at a given hyperlink index.
		"""
		log.debug("Starting at hyperlink index %d" % startIndex)
		for index in xrange(startIndex, hypertext.nHyperlinks if direction == "next" else -1, 1 if direction == "next" else -1):
			hl = hypertext.hyperlink(index)
			obj = IAccessible(IAccessibleObject=hl.QueryInterface(IAccessibleHandler.IAccessible2), IAccessibleChildID=0)
			yield obj
			#for subObj in self._iterEmbeddedObjs(obj.iaHypertext, 0 if direction == "next" else obj.iaHypertext.nHyperlinks - 1, direction):
				#yield subObj

	NODE_TYPES_TO_ROLES = {
		"link": controlTypes.ROLE_LINK,
		"graphic": controlTypes.ROLE_GRAPHIC,
	}

	def _iterNodesByType(self, nodeType, direction="next", pos=None):
		role = self.NODE_TYPES_TO_ROLES.get(nodeType)
		if not role:
			raise NotImplementedError
		if not pos:
			pos = self.makeTextInfo(textInfos.POSITION_FIRST if direction == "next" else textInfos.POSITION_LAST)
		obj = pos.innerTextInfo._startObj
		# Find the first embedded object in the requested direction.
		# Use the text, as enumerating IAccessibleHypertext means more cross-process calls.
		offset = pos.innerTextInfo._start._startOffset
		if direction == "next":
			text = obj.IAccessibleTextObject.text(offset + 1, obj.IAccessibleTextObject.nCharacters)
			embed = text.find(u"\uFFFC")
			if embed != -1:
				embed += offset + 1
		else:
			text = obj.IAccessibleTextObject.text(0, offset)
			embed = text.rfind(u"\uFFFC")
		log.debug("%s embedded object from offset %d: %d" % (direction, offset, embed))
		hli = -1 if embed == -1 else obj.iaHypertext.hyperlinkIndex(embed)
		while True:
			if hli != -1:
				for embObj in self._iterEmbeddedObjs(obj.iaHypertext, hli, direction):
					if embObj.role == role:
						ti = self.makeTextInfo(embObj)
						yield browseMode.TextInfoQuickNavItem(nodeType, self, ti)
			# No more embedded objects here.
			# We started in an embedded object, so continue in the parent.
			if obj == self.rootNVDAObject:
				log.debug("At root, stopping")
				break # Can't go any further.
			log.debug("Continuing in parent")
			# Get the index of the embedded object we just came from.
			hl = obj.IAccessibleObject.QueryInterface(IAccessibleHandler.IAccessibleHyperlink)
			offset = hl.startIndex
			obj = obj.parent
			hli = obj.iaHypertext.hyperlinkIndex(offset)
			# Continue the walk from the next embedded object.
			hli += 1 if direction == "next" else -1

class BookPageViewTextInfo(MozillaCompoundTextInfo):

	def _get_locationText(self):
		curLocation=self.obj.IA2Attributes.get('kindle-first-visible-location-number')
		maxLocation=self.obj.IA2Attributes.get('kindle-max-location-number')
		pageNumber=self.obj.pageNumber
		# Translators: A position in a Kindle book
		# xgettext:no-python-format
		text=_("{bookPercentage}%, location {curLocation} of {maxLocation}").format(bookPercentage=int((float(curLocation)/float(maxLocation))*100),curLocation=curLocation,maxLocation=maxLocation)
		if pageNumber:
			# Translators: a page in a Kindle book
			text+=", "+_("Page {pageNumber}").format(pageNumber=pageNumber)
		return text

	def getTextWithFields(self, formatConfig=None):
		items = super(BookPageViewTextInfo, self).getTextWithFields(formatConfig=formatConfig)
		for item in items:
			if isinstance(item, textInfos.FieldCommand) and item.command == "formatChange":
				if formatConfig['reportPage']:
					item.field['page-number'] = self.obj.pageNumber
		return items

	def getFormatFieldSpeech(self, attrs, attrsCache=None, formatConfig=None, reason=None, unit=None, extraDetail=False , initialFormat=False, separator=speech.CHUNK_SEPARATOR):
		out = ""
		highlight = attrs.get("highlight")
		oldHighlight = attrsCache.get("highlight") if attrsCache is not None else None
		if oldHighlight != highlight:
			# Translators: Reported when text is highlighted.
			out += (_("highlight") if highlight
				# Translators: Reported when text is not highlighted.
				else _("no highlight")) + separator
		popular = attrs.get("kindle-popular-highlight-count")
		oldPopular = attrsCache.get("kindle-popular-highlight-count") if attrsCache is not None else None
		if oldPopular != popular:
			# Translators: Reported in Kindle when text has been identified as a popular highlight;
			# i.e. it has been highlighted by several people.
			# %s is replaced with the number of people who have highlighted this text.
			out += (_("%s highlighted") % popular if popular
				# Translators: Reported when moving out of a popular highlight.
				else _("out of popular highlight")) + separator
		out += super(BookPageViewTextInfo, self).getFormatFieldSpeech(attrs, attrsCache=attrsCache, formatConfig=formatConfig, reason=reason, unit=unit, extraDetail=extraDetail , initialFormat=initialFormat, separator=separator)
		return out

	def updateSelection(self):
		# For now, we need to do all selection on the root.
		# This means only entire embedded objects can be selected.
		if self._startObj == self.obj:
			sel = self._start.copy()
		else:
			log.debug("Start object isn't root, getting embedding")
			sel = self._getEmbedding(self._startObj)
			assert sel.obj == self.obj
		if self._endObj == self.obj:
			end = self._end
		else:
			log.debug("End object isn't root, getting embedding")
			end = self._getEmbedding(self._endObj)
			assert end.obj == self.obj
		sel.setEndPoint(end, "endToEnd")
		log.debug("Setting selection to (%d, %d)" % (sel._startOffset, sel._endOffset))
		sel.updateSelection()

class BookPageView(DocumentWithPageTurns,IAccessible):
	"""Allows navigating page text content with the arrow keys."""

	treeInterceptorClass=BookPageViewTreeInterceptor
	TextInfo=BookPageViewTextInfo
	shouldAllowIAccessibleFocusEvent=True

	def _get_pageNumber(self):
		try:
			first=self.IA2Attributes['kindle-first-visible-physical-page-label']
			last=self.IA2Attributes['kindle-last-visible-physical-page-label']
		except KeyError:
			try:
				first=self.IA2Attributes['kindle-first-visible-physical-page-number']
				last=self.IA2Attributes['kindle-last-visible-physical-page-number']
			except KeyError:
				return None
		if first!=last:
			return "%s to %s"%(first,last)
		else:
			return first

	def turnPage(self,previous=False):
		try:
			self.IAccessibleActionObject.doAction(1 if previous else 0)
		except COMError:
			raise RuntimeError("no more pages")
		startTime=curTime=time.time()
		while (curTime-startTime)<0.5:
			api.processPendingEvents(processEventQueue=False)
			# should  only check for pending pageChange for this object specifically, but object equality seems to fail sometimes?
			if eventHandler.isPendingEvents("pageChange"):
				self.invalidateCache()
				break
			time.sleep(0.05)
			curTime=time.time()
		else:
			raise RuntimeError("no more pages")

class PageTurnFocusIgnorer(IAccessible):

	def _get_shouldAllowIAccessibleFocusEvent(self):
		# When in a page turn, Kindle  fires focus on the new page in the table of contents treeview.
		# We must ignore this focus event as it is a hinderance to a screen reader user while reading the book.
		if self.appModule.inPageTurn:
			return False
		return super(PageTurnFocusIgnorer,self).shouldAllowIAccessibleFocusEvent

class AppModule(appModuleHandler.AppModule):

	inPageTurn=False

	def chooseNVDAObjectOverlayClasses(self,obj,clsList):
		if isinstance(obj,IAccessible):
			clsList.insert(0,PageTurnFocusIgnorer)
			if hasattr(obj,'IAccessibleTextObject') and obj.name=="Book Page View":
				clsList.insert(0,BookPageView)
		return clsList

	def event_NVDAObject_init(self, obj):
		if isinstance(obj, IAccessible) and isinstance(obj.IAccessibleObject, IAccessibleHandler.IAccessible2) and obj.role == controlTypes.ROLE_LINK:
			ariaRoles = obj.IA2Attributes.get("xml-roles", "").split(" ")
			for ar in ariaRoles:
				role = aria.ariaRolesToNVDARoles.get(ar)
				if role:
					obj.role = role
					return
