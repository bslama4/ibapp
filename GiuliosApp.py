import asyncio
import math
import os

#import matplotlib.pyplot as plt
from tkinter import Canvas

from PyQt5.QtCore import QSize
#from matplotlib.figure import Figure
#from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as Canvas
import datetime as dt
import time
import pandas as pd
import logging
import talib as ta
import PyQt5.QtWidgets as qt
from PyQt5 import QtWidgets
from ib_insync import IB, util, MarketOrder
from ib_insync.order import (
    BracketOrder, LimitOrder, Order, OrderState, OrderStatus, StopOrder, Trade)
from ib_insync.objects import AccountValue, TradeLogEntry
from ib_insync.contract import *  # noqa
from ib_insync.order import (
    BracketOrder, LimitOrder, Order, OrderState, OrderStatus, StopOrder, Trade)
from ib_insync.util import dataclassRepr, isNan
from typing import ClassVar, List, Optional, Union
from datetime import datetime
from eventkit import Event, Op
from matplotlib.figure import Figure

nan = float('nan')
logfilename = os.path.join('D:\Work\Work\Giulio\logs', datetime.now().strftime("%Y%m%d-%H%M%S"))
#logfilename = os.path.join(os.getcwd(), datetime.now().strftime("%Y%m%d-%H%M%S"))
logfilename += '.txt'
logging.basicConfig(filename=logfilename,format='%(asctime)s,%(msecs)d %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s',
                    datefmt='%Y-%m-%d:%H:%M:%S',
                    level=logging.DEBUG)
logger = logging.getLogger(__name__)


def lowerHundred(number):
    return int(math.floor(number / 100.0)) * 100


class ohlcData:
    contract: Optional[Contract] = None
    volume: float = nan
    open: float = nan
    high: float = nan
    low: float = nan
    close: float = nan

    def __eq__(self, other):
        return self is other

    def __hash__(self):
        return id(self)

class HistoricalTable(qt.QTableWidget):
    headers = [
        'symbol', 'MA50', 'MA200'] #'open', 'high', 'low', 'close', 'MA_close']

    def __init__(self, parent=None):
        logging.debug("init")
        qt.QTableWidget.__init__(self, parent)
        self.reqId2Row = {}
        self.setColumnCount(len(self.headers))
        self.setHorizontalHeaderLabels(self.headers)
        self.setAlternatingRowColors(True)

    def __contains__(self, contract):
        assert contract.conId
        return contract.conId in self.reqId2Row

    def addHistoricalData(self, reqId, contract):
        logging.debug("hist - " + str(self.rowCount()))
        row = self.rowCount()
        logging.debug(row)
        self.insertRow(row)
        logging.debug(contract.conId)
        self.reqId2Row[reqId] = row
        for col in range(len(self.headers)):
            item = qt.QTableWidgetItem('-')
            self.setItem(row, col, item)
            logging.debug("item - " + str(row) + " " + str(col) + " " + str(item))
        item = self.item(row, 0)
        logging.debug("setting item")
        item.setText(contract.symbol + (
            contract.currency if contract.secType == 'CASH'
            else ''))
        logging.debug("setting item done")
        self.resizeColumnsToContents()

    def updateData(self, reqId, ma50, ma200):
        row = self.reqId2Row[reqId]
        val = self.item(row, 1)
        val.setText(str(ma50))
        val = self.item(row, 2)
        val.setText(str(ma200))

    def clearData(self):
        self.setRowCount(0)
        self.reqId2Row.clear()

class MovingAverages():
    def __init__(self, ib, symbol: str = '', reqId: float = 0):#, ma50: float = 0, ma200: float = 0):
        self.ib = ib
        self.symbol = symbol
        self.reqId = reqId
        self.firstma50 = 0
        self.firstma200 = 0
        self.firstSignal = True
        self.GCCheck = True
        self.bars = []
        self.ma50: []
        self.ma200 = []
        self.ma50val = 0
        self.ma200val = 0
        self.bid = 0
        self.ask = 0
        self.availCash = 0
        self.isOrderActive = False
        self.isGCOrder = False
        self.isGCBuyOrder = False
        self.isGCSellOrder = False
        self.sentGCTO = False
        self.isDCOrder = False
        self.isDCBuyOrder = False
        self.isDCSellOrder = False
        self.sentDCTO = False
        self.gcpOrderId: int = 0
        self.gcpStatus: str = ''
        self.size: int = 0
        self.gcpFilled: int = 0
        self.gcpRemaining: int = 0
        self.gctpOrderId: int = 0
        self.gctpStatus: str = ''
        self.gcslOrderId: int = 0
        self.gcslStatus: str = ''
        self.gcBoughtSize: int = 0
        self.gcAvgFillPrice: float = 0.0
        self.gcLastFillPrice: float = 0.0
        self.dcpOrderId: int = 0
        self.dctpOrderId: int = 0
        self.dcStatus: str = ''
        self.dcFilled: int = 0
        self.dcRemaining: int = 0
        self.dcAvgFillPrice: float = 0.0
        self.dcLastFillPrice: float = 0.0
        #print("init complete")

    def setMAs(self, df):
        self.ma50 = ta.MA(df['close'], 50)
        self.ma200 = ta.MA(df['close'], 200)

    def TrailBracketOrder(self, parentOrderId, childOrderId, action, quantity, limitPrice, trailAmount):

        # This will be our main or "parent" order
        parent = Order()
        parent.orderId = parentOrderId
        parent.action = action
        parent.orderType = "LMT"
        parent.totalQuantity = 1000 #quantity
        parent.lmtPrice = limitPrice
        parent.transmit = False

        stopLoss = Order()
        stopLoss.orderId = childOrderId
        logging.info("Action is " + action)
        if action == "Buy":
            stopLoss.action = "Sell"
            stopLoss.trailStopPrice = limitPrice - (limitPrice * .02)
        if action == "Sell":
            stopLoss.action = "Buy"
            stopLoss.trailStopPrice = limitPrice + (limitPrice * .02)
        stopLoss.orderType = "TRAIL"
        stopLoss.auxPrice = limitPrice #trailAmount
        stopLoss.totalQuantity = 1000 #quantity
        stopLoss.parentId = parentOrderId
        stopLoss.transmit = True

        bracketOrder = [parent, stopLoss]
        return bracketOrder

    def checkGCDC(self, availCash):
        logging.debug("avail cash - " + str(availCash))
        if (self.firstSignal == True):
            self.firstma50 = round(self.ma50.tail(1).item(), 6)
            self.firstma200 = round(self.ma200.tail(1).item(), 6)
            self.firstSignal = False
            if (self.firstma50 < self.firstma200):
                logging.info("checking golden cross for " + self.symbol + " : mas - " + str(self.firstma50) + " " + str(self.firstma200))
            else:
                logging.info("checking death cross for " + self.symbol + " : mas - " + str(self.firstma50) + " " + str(self.firstma200))
                self.GCCheck = False
                #self.MADict[symbol] = ma
        else:
            prevma50 = self.getMa50()
            prevma200 = self.getMa200()
            currma50 = round(self.ma50.tail(1).item(), 6)
            currma200 = round(self.ma200.tail(1).item(), 6)
            if(self.GCCheck == True):
                logging.debug("golden cross check for " + self.symbol)
                if (self.isOrderActive == True):
                    logging.info("previous order is active for " + self.symbol)
                if((prevma50 < prevma200) and (currma50 > currma200)):
                    logging.info("prev mas - " + str(prevma50) + " " + str(prevma200))
                    logging.info("curr mas - " + str(currma50) + " " + str(currma200))
                    logging.info("curr bid and ask vals - " + str(self.bid) + " " + str(self.ask))
                    logging.info(("golden cross occured for " + self.symbol))
                    self.GCCheck = False
                    if (self.isOrderActive == True):
                        logging.info("previous order is active")
                    if (self.isOrderActive == False):
                        self.isOrderActive = True
                        self.isGCBuyOrder = True
                        #order = TrailOrder("Buy", 1000, self.ask, 2)
                        #trade = self.ib.placeOrder(self.contract, order)
                        self.gcpOrderId = self.ib.client.getReqId()
                        #order = self.TrailBracketOrder(self.gcpOrderId, self.gctpOrderId, "Buy", 1000, self.ask, (self.ask * .02))
                        logging.info("Placing buy order for " + self.symbol)
                        order = Order()
                        order.orderId = self.gcpOrderId
                        order.action = "Buy"
                        order.orderType = "MKT"
                        cash = availCash * .01
                        logging.info(str(cash) + " " + str(availCash) + " " + str(self.ask))
                        quantity = (cash / self.ask)
                        logging.info("quantity - " + str(quantity))
                        #quantity = quantity * .01
                        quantity = lowerHundred(quantity)
                        logging.info("quantity - " + str(quantity))
                        logging.info("order quantity - " + str(quantity))
                        order.totalQuantity = quantity
                        return order
                        #self.MADict[symbol] = ma

            else:
                logging.debug("death cross check for " + self.symbol)
                if (self.isOrderActive == True):
                    logging.info("previous order is active for " + self.symbol)
                if ((prevma50 > prevma200) and (currma50 < currma200)):
                    logging.info("prev mas - " + str(prevma50) + " " + str(prevma200))
                    logging.info("curr mas - " + str(currma50) + " " + str(currma200))
                    logging.info("curr bid and ask vals - " + str(self.bid) + " " + str(self.ask))
                    logging.info(("death cross occured for " + self.symbol))
                    self.GCCheck = True
                    if (self.isOrderActive == True):
                        logging.info("previous order is active")
                    if (self.isOrderActive == False):
                        self.isOrderActive = True
                        self.isDCOrder = True
                        self.isDCSellOrder = True
                        #order = TrailOrder("Sell", 1000, self.bid, 2)
                        #trade = self.ib.placeOrder(self.contract, order)
                        self.dcpOrderId = self.ib.client.getReqId()
                        self.dctpOrderId = self.ib.client.getReqId()
                        #order = self.TrailBracketOrder(self.dcpOrderId, self.dctpOrderId, "Sell", 1000, self.bid, (self.bid * .02))
                        logging.info("Placing sell order for " + self.symbol)
                        order = Order()
                        order.orderId = self.dcpOrderId
                        order.action = "Sell"
                        order.orderType = "MKT"
                        cash = availCash * .01
                        logging.info(str(cash) + " " + str(availCash) + " " + str(self.ask))
                        quantity = cash/self.bid
                        logging.info("quantity - " + str(quantity))
                        #quantity = quantity * .01
                        quantity = lowerHundred(quantity)
                        logging.info("quantity - " + str(quantity))
                        logging.info("availcash and bid - " + str(availCash) + " " + str(self.bid))
                        order.totalQuantity = quantity
                        return order
                        #self.MADict[symbol] = ma
        return None


    def setMa50(self, ma50val):
        self.ma50val = ma50val

    def setMa200(self, ma200val):
        self.ma200val = ma200val

    def getMa50(self) -> str:
        return self.ma50val

    def getMa200(self) -> str:
        return self.ma200val

class TrailOrder(Order):

    def __init__(self, action, totalQuantity, trailStopPrice, trailingPercent, **kwargs):
        Order.__init__(
            self, orderType='TRAIL', action=action,
            totalQuantity=totalQuantity, trailStopPrice=trailStopPrice, trailingPercent=trailingPercent, **kwargs)

"""def BracketOrder(parentOrderId, childOrderId, action, limitPrice, trailAmount):

    #This will be our main or "parent" order
    parent = Order()
    parent.orderId = parentOrderId
    parent.action = action
    parent.orderType = "LMT"
    #parent.totalQuantity = quantity
    parent.lmtPrice = limitPrice
    parent.transmit = False

    stopLoss = Order()
    stopLoss.orderId = childOrderId
    stopLoss.action = "SELL" if action == "BUY" else "BUY"
    stopLoss.orderType = "TRAIL"
    stopLoss.auxPrice = trailAmount
    stopLoss.trailStopPrice = limitPrice - trailAmount
    #stopLoss.totalQuantity = quantity
    stopLoss.parentId = parentOrderId
    stopLoss.transmit = True

    bracketOrder = [parent, stopLoss]
    return bracketOrder """

class Window(qt.QWidget):
    def __init__(self, host, port, clientId):
        qt.QWidget.__init__(self)
        self.setWindowTitle("Giulio's App")
        self.table = HistoricalTable()
        self.symbolInput = qt.QLineEdit()
        self.connectButton = qt.QPushButton('Connect')
        self.connectButton.setStyleSheet("border: 1px solid black; background: white");
        self.connectButton.resize(100, 32)
        self.connectButton.setGeometry(200, 150, 100, 40)
        self.connectButton.clicked.connect(self.onConnectButtonClicked)
        self.displayButton = qt.QPushButton('Display values')
        self.displayButton.setStyleSheet("border: 1px solid black; background: white");
        self.displayButton.resize(100, 32)
        self.displayButton.clicked.connect(self.onDisplayButtonClicked)
        self.reqDataButton = qt.QPushButton('ReqData')
        self.reqDataButton.setStyleSheet("border: 1px solid black; background: white");
        self.reqDataButton.resize(100, 32)
        self.reqDataButton.setGeometry(200, 150, 100, 40)
        self.reqDataButton.clicked.connect(self.onReqData)
        #self.reqDataButton.clicked.connect(self.onReqDataButtonClicked)
        self.closePosButton = qt.QPushButton('Close Postions')
        self.closePosButton.setStyleSheet("border: 1px solid black; background: white");
        self.closePosButton.resize(100, 32)
        self.closePosButton.setGeometry(200, 150, 100, 40)
        self.closePosButton.clicked.connect(self.onClosePosButtonClicked)
        self.cancelAllButton = qt.QPushButton('CancelAll')
        self.cancelAllButton.setStyleSheet("border: 1px solid black; background: white");
        self.cancelAllButton.resize(100, 32)
        self.cancelAllButton.setGeometry(200, 150, 100, 40)
        self.cancelAllButton.clicked.connect(self.onCancelAllButtonClicked)
        layout = qt.QFormLayout(self)
        layout.addWidget(self.symbolInput)
        layout.addWidget(self.table)
        layout.addWidget(self.connectButton)
        layout.addWidget(self.reqDataButton)
        layout.addWidget(self.cancelAllButton)
        layout.addWidget(self.closePosButton)
        #layout.addStretch()
        self.MAList = []
        self.MADict = {}
        self.xs = []
        self.ys = []
        # layout.addWidget(self.fig)
        self.connectInfo = (host, port, clientId)
        self.ib = IB()
        self.headers = [
            'symbol', 'bidSize', 'bid', 'ask', 'askSize',
            'last', 'lastSize', 'close']
        self.id = 1;
        self.firstSignal = True
        self.isConnectionBroken = False
        self.closePos = False
        self.firstma50 = 0
        self.firstma200 = 0
        self.availableCash = 0
        self.ib.positionEvent += self.position_cb
        self.ib.orderStatusEvent += self.order_status_cb
        self.ib.execDetailsEvent += self.exec_details_cb
        self.ib.errorEvent += self.error_cb
        self.ib.accountSummaryEvent += self.accountSummary
        self.ib.pendingTickersEvent += self.onPendingTickers

        # self.ib.pendingTickersEvent += self.table.onPendingTickers

    def onConnectButtonClicked(self, _):
        logging.debug("isconnected: " + str(self.ib.isConnected()))
        if self.ib.isConnected():
            self.ib.disconnect()
            logging.debug("clearing data")
            self.table.clearData()
            self.connectButton.setText('Connect')
            logging.debug("done")
        else:
            logging.debug("trying to connect")
            # ib = IB()
            # ib.connect('127.0.0.1', 7497, clientId=3)
            #self.reqData()
            self.ib.connect('127.0.0.1', 7497, clientId=1)  # *self.connectInfo)
            logging.debug("connected - ")  # + self.ib.isConnected())
            # self.ib.reqMarketDataType(2)
            self.connectButton.setText('Disconnect')
            self.ib.reqAccountSummary()

    def onCancelAllButtonClicked(self):
        logging.info("Cancelling all open orders")
        #self.ib.connect('127.0.0.1', 7497, clientId=2)  # *self.connectInfo)
        self.reqGlobalCancel()

    def textchanged(text):
        print("contents of text box: " + text)

    def onDisplayButtonClicked(self, _):
        logging.debug("MA values")
        for ma in self.MAList:
            logging.debug("symbol - " + " " + ma.symbol)
            logging.debug(str(ma.firstma50) + " " + str(ma.firstma200) + " " + str(ma.firstSignal) + " " + str(
                ma.ma50) + " " + str(ma.ma200))
        for x in self.MADict:
            logging.debug(x)
        for x in self.MADict.values():
            logging.debug("dict values - " + str(x.firstSignal) + " " + x.symbol + " " + str(x.firstma50) + " " + str(
                x.firstma200) + " " + str(x.ma50) + " " + str(x.ma200))

    def onClosePosButtonClicked(self):
        logging.info("Closing all positions")
        self.closePos = True
        self.ib.reqPositions()

    def onReqData(self):
        #self.reqGlobalCancel()
        """for symbol in ('EURUSD', 'USDJPY', 'EURGBP', 'USDCAD',
                       'EURCHF', 'AUDUSD', 'AUDCAD', 'NZDUSD', 'GBPUSD'):
            logging.debug("requesting for " + symbol)
            self.add_historical(f"Forex('{symbol}')")"""

        self.add_historical("Stock('TSLA', 'SMART', 'USD')")
        self.add_historical("Stock('IBM', 'SMART', 'USD')")
        self.add_historical("Stock('MSFT', 'SMART', 'USD')")
        self.add_historical("Stock('FB', 'SMART', 'USD')")
        #symbol = self.symbolInput.text()
        #self.add_historical(f"Stock('{symbol}', 'SMART', 'USD')")

    def onReqDataButtonClicked(self):
        print("Requesting data for " + self.symbolInput.text())
        symbol = self.symbolInput.text()
        #self.add_historical(f"Forex('{symbol}')")
        self.add_historical(f"Stock('{symbol}', 'SMART', 'USD')")

    def add_historical(self, text=''):
        logging.debug("text - " + text)
        logger.debug("logging")
        text = text or self.edit.text()
        if text:
            logging.debug('eval text ')  # + eval(text))
            contract = eval(text)
            print("contract symbol is " + contract.symbol)
            self.ib.reqMktData(contract, '', False, False, None)
            logging.debug("requesting historical and mkt data for " + text)
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime='',
                durationStr='2000 S',
                barSizeSetting='10 secs',
                whatToShow='MIDPOINT',
                useRTH=True,
                formatDate=1,
                keepUpToDate=True)
            #self.ib.reqMktData(contract, '', False, False, None)
            #logging.info(bars[-1])
            logging.debug("sectype " + str(
                bars.reqId) + " " + str(bars.contract.conId) + " " + bars.contract.secType + " " + bars.contract.symbol + " " + bars.contract.currency)
            self.table.addHistoricalData(bars.reqId, contract)
            df = util.df(bars)
            close = pd.DataFrame(df, columns=['close'])
            logging.debug("close ")
            logging.debug(close)
            symbol = bars.contract.symbol + (
                bars.contract.currency if bars.contract.secType == 'CASH'
                else '')
            logging.info("symbol - " + symbol)
            ma = MovingAverages(self.ib, symbol, bars.reqId) #, round(ma50.tail(1).item(), 6), round(ma200.tail(1).item(), 6))
            ma.setMAs(df)
            self.MAList.append(ma)
            self.MADict[symbol] = ma
            self.table.updateData(bars.reqId, round(ma.ma50.tail(1).item(), 6), round(ma.ma200.tail(1).item(), 6))
            bars.updateEvent += self.onBarUpdate
            logging.debug("reqid is " + str(
                bars.reqId) + " for " + bars.contract.symbol + " " + bars.contract.currency + " , sectype - " + bars.contract.secType)

    async def accountSummaryAsync(self, account: str = '') -> \
            List[AccountValue]:
        if not self.wrapper.acctSummary:
            await self.reqAccountSummaryAsync()
        if account:
            return [v for v in self.wrapper.acctSummary.values()
                    if v.account == account]
        else:
            return list(self.wrapper.acctSummary.values())

    def accountSummary(self, account: str = '') -> List[AccountValue]:
        if (account.tag == 'BuyingPower'):
            logging.info('account buying power - ' + account.value)
            accVal: float = 0.0
            accVal = account.value
            #self.availableCash = float(accVal)
            #self.availableCash = round(self.availableCash, 2)
            availableCash = float(accVal)
            availableCash = round(availableCash, 2)
            self.availableCash += availableCash
            logging.info('available cash - ' + str(self.availableCash))
        logging.debug("account summary:: " + str(account.account) + " " + account.tag + " " + account.value)

        return [] #self._run(self.accountSummaryAsync(account))

    def error_cb(self, reqId, errorCode, errorString, contract):
        logging.error("error: " + str(reqId) + " , " + str(errorCode) + " , " + str(errorString))
        logging.error("string - " + str(errorString))
        """if(errorCode == 1100):
            logging.error("Connectivity between IB and TWS has been lost")
            self.isConnectionBroken = True
        if (errorCode == 1300):
            logging.error("socket connection dropped")
            self.isConnectionBroken = True
        if(errorCode == 2105):
            logging.error("HMDS data farm connection is broken")
        if ((errorCode == 2104 or errorCode == 2106) and self.isConnectionBroken == True):
            logging.info("HMDS data farm connection has been restored")
            self.reqData()"""


    def reqGlobalCancel(self):
        """
        Cancel all active trades including those placed by other
        clients or TWS/IB gateway.
        """
        self.ib.reqGlobalCancel()
        logging.info('reqGlobalCancel')

    def position_cb(self, position):
        if(position.position == 0):
            return
        if(self.closePos == True):
            symbol = position.contract.symbol + (
                position.contract.currency if position.contract.secType == 'CASH'
                else '')
            print("position for " + position.account + " - " + symbol + " " + position.contract.exchange + " " + position.contract.primaryExchange + " :: " + str(position.position) + "  Avg Cost - " + str(position.avgCost))
            if(position.position > 0):
                action = 'SELL'
            else:
                action = 'BUY'

            #order = Order(orderType='MKT', action=action, totalQuantity=position.position)
            exchange = 'IDEALPRO' if position.contract.exchange == '' else 'SMART'
            currency = position.contract.currency
            secType = position.contract.secType

            order = Order()
            order.orderId = self.ib.client.getReqId()
            order.action = action
            order.orderType = "MKT"
            order.exchange = exchange
            if(position.position < 0):
                position = -(position.position)

            print("position - " + str(position))
            quantity = position
            order.totalQuantity = quantity
            contract = Contract()
            contract.symbol = symbol
            contract.secType = secType
            contract.currency = currency

            contract.exchange = exchange
            contract.primaryExchange = exchange

            trade = self.ib.placeOrder(contract, order)

    def order_status_cb(self, trade):
        if (self.closePos == True or not self.MADict):
            return
        symbol = trade.contract.symbol + (trade.contract.currency if trade.contract.secType == 'CASH' else '')
        logging.info("OrderId, Status, avgFillPrice, filled and remaining for  " + symbol + " - " + str(trade.order.orderId) + " " + trade.orderStatus.status  + " " + str(trade.orderStatus.avgFillPrice) + " " + str(trade.orderStatus.filled) + " " + str(trade.orderStatus.remaining))

        ma = self.MADict[symbol]
        if(ma.isOrderActive == True):
            if(ma.GCCheck == False):
                logging.info("checking for gcorder")
            if(ma.GCCheck == True):
                logging.info("checking for dcorder")

    def exec_details_cb(self, trade, fill):
        if(self.closePos == True or not self.MADict):
            return
        symbol = trade.contract.symbol + (
            trade.contract.currency if trade.contract.secType == 'CASH'
            else '')
        ma = self.MADict[symbol]

        isdone = trade.isDone()
        print("isdone - " + str(isdone))
        if (ma.isDCOrder == True):
            print("DC order is active")
        remaining = trade.remaining()
        if(trade.isDone() == False):
            logging.info("trade isnt done yet for " + symbol + " , remaining - " + str(remaining))
        if(remaining == 0):
            #ma.isOrderActive = False
            totalFilled = fill.execution.shares # trade.orderStatus.filled
            cumQty = fill.execution.cumQty
            logging.info("Total filled, cumQty and average fill price for " + symbol + " - " + str(totalFilled) + " " + str(cumQty) + " " + str(fill.execution.avgPrice)) #str(trade.orderStatus.avgFillPrice))
            if (ma.isGCSellOrder == True):
                logging.info("GC Sell order is done for " + symbol)
                self.availableCash += (fill.execution.cumQty * fill.execution.avgPrice)
                ma.isOrderActive = False
                ma.isGCSellOrder = False
            if (ma.isGCBuyOrder == True):
                logging.info("GC Buy order is done for " + symbol)
                self.availableCash -= (fill.execution.cumQty * fill.execution.avgPrice)
                #send sell
                trailSP = fill.execution.avgPrice * .8
                ma.gctpOrderId = self.ib.client.getReqId()
                order = Order()
                order.orderId = ma.gctpOrderId
                order.action = "SELL"
                order.orderType = "TRAIL"
                order.totalQuantity = fill.execution.cumQty
                order.trailingPercent = 2
                order.trailStopPrice = trailSP
                SPTrade = self.ib.placeOrder(trade.contract, order)
                ma.isGCBuyOrder = False
                ma.isGCSellOrder = True

            if (ma.isDCBuyOrder == True):
                logging.info("DC Buy order is done for " + symbol)
                self.availableCash -= (fill.execution.cumQty * fill.execution.avgPrice)
                ma.isOrderActive = False
                ma.isDCBuyOrder = False

            if (ma.isDCSellOrder == True):
                logging.info("DC Sell order is done for " + symbol)
                self.availableCash += (fill.execution.cumQty * fill.execution.avgPrice)
                #send buy
                trailSP = fill.execution.avgPrice * 1.2
                order = Order()
                self.dctpOrderId = self.ib.client.getReqId()
                order.orderId = self.dctpOrderId
                order.action = "BUY"
                order.orderType = "TRAIL"
                order.totalQuantity = fill.execution.cumQty
                order.trailingPercent = 2
                order.trailStopPrice = trailSP
                SPTrade = self.ib.placeOrder(trade.contract, order)
                ma.isGCBuyOrder = False
                ma.isDCBuyOrder = True

            logging.info("exec details for " + symbol + " with orderid " + str(fill.execution.orderId))

        #if(fill.execution.side == "Sell"):
        #    self.availableCash += fill.execution.price


    def onPendingTickers(self, tickers):
        for ticker in tickers:
            logging.debug("ticker - " + str(ticker.contract.conId) + " " + str(ticker.contract.secType) + " " + ticker.contract.symbol + " " + ticker.contract.currency)
            for col, header in enumerate(self.headers):
                if col == 0:
                    continue
                val = getattr(ticker, header)
                symbol = ticker.contract.symbol + (
                    ticker.contract.currency if ticker.contract.secType == 'CASH'
                    else '')
                if(symbol in self.MADict):
                    logging.debug(symbol + " key is present")
                    ma = self.MADict[symbol]
                    logging.debug("Values - " + str(ticker.contract.secType) + " " + str(
                        ticker.contract.conId) + " " + symbol + " " + str(header) + " " + str(col) + " val- " + str(
                        val))
                    if (str(header) == 'bid'):
                        ma.bid = val
                    if (str(header) == 'ask'):
                        ma.ask = val
                else:
                    logging.error(symbol + " key is not present")

    def onBarUpdate(self, bars, hasNewBar):
        self.xs.append(dt.datetime.now().strftime('%H:%M:%S.%f'))
        # logging.debug("bar update " + str(hasNewBar) + " for " + str(bars.reqId))
        logging.debug(bars[-1])
        symbol = bars.contract.symbol + (
            bars.contract.currency if bars.contract.secType == 'CASH'
            else '')
        ma = self.MADict[symbol]
        logging.debug("update for " + ma.symbol)
        df = util.df(bars)
        # logging.debug(df)
        ma.setMAs(df)
        ma50 = ta.MA(df['close'], 50)
        ma200 = ta.MA(df['close'], 200)
        self.ys.append(ma50)

        self.xs = self.xs[-50:]
        self.ys = self.ys[-50:]

        logging.debug("aval cash and lH - " + str(self.availableCash))
        orderList = ma.checkGCDC(self.availableCash)
        if(orderList is not None):
            logging.info("aval cash and lH - " + str(self.availableCash))
            self.ib.placeOrder(bars.contract, orderList)

        if(ma.isOrderActive == False and ma.GCCheck == True):
            logging.debug("order is not active and gccheck is true")
        #self.MADict[symbol] = ma

        ma.setMa50(round(ma50.tail(1).item(), 6))
        ma.setMa200(round(ma200.tail(1).item(), 6))
        self.MADict[symbol] = ma

        logging.debug("MAs for " + str(bars.contract.secType) + " " + str(
            bars.contract.symbol) + " " + bars.contract.currency + " , reqid: " + str(bars.reqId) + " " + str(
            ma50.values[-1]) + " " + str(ma200.values[-1]) + " : " + str(ma50.tail(1).item()) + " " + str(ma200.tail(1).item()))
        self.table.updateData(bars.reqId, round(ma50.tail(1).item(), 6), round(ma200.tail(1).item(), 6))

    def closeEvent(self, ev):
        logging.debug("closing")
        asyncio.get_event_loop().stop()


if __name__ == '__main__':
    util.patchAsyncio()
    util.useQt()
    # util.useQt('PySide2')
    window = Window('127.0.0.1', 7497, 1)
    window.resize(600, 400)
    window.show()
    IB.run()
    loop = asyncio.get_event_loop()
