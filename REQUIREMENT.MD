i want to trade in Nifty options on 3 minute timeframe. Conditions for entry is the nifty-options-buying-strategy.pine file. I am using zerodha broker for trading Default settings for the all the indicatore in pine available in the screenshot.

Strike selection is based on the 1% value of the underlying asset. Eg: if Nifty 50 is trading 20750 then the 1% value of the underlying asset is 207.50 or nearest value. it is only options buying strategy. if the underlying asset is increasing then buy call option and if the underlying asset is decreasing then buy put option..

Lot size is fixed. starting with 1 lot.

Always buy current week contract and it is intraday option, the position should be squared off at the 3:00 PM IST.

All the entry, exit, buy signal, exit signal should be notified to my discord server webhook url - https://discord.com/api/webhooks/1568208979576881263/RNJYGAZe-jGwxTpBHmQDFTuOlzOx6SSUr22dh9yZhzlFhstdHL-gal4TjEbmwgt7dZ6w

All the secert keys and other infromation should be stored in the .env file and i need a flag to disable the order placement in the exchange.

I will be deploying this application in the raspberry pi 5 prodcution server.
Analyse the pine script file and convert it to the application python script.
