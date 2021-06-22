""" Resident Advisor ticket sales queue. """
import argparse
import os
import time
import json
import string
import logging
import secrets
import datetime
import platform

import telegram
from selenium import webdriver

from client import TelegramClient


f_handler = logging.FileHandler(os.path.join('logs', f'{os.path.splitext(os.path.basename(__file__))[0]}.log'))
f_handler.setFormatter(logging.Formatter('[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s'))
logging.basicConfig(format='[%(asctime)s] {%(filename)s} %(levelname)s - %(message)s', level=logging.INFO)
logging.getLogger('').addHandler(f_handler)
logger = logging.getLogger(__file__)


def take_screenshot(driver, path='debug'):
    screenshot_filename = f"RA Screenshot {datetime.datetime.now().ctime().replace(':', '_')}.png"
    driver.save_screenshot(os.path.join(path, screenshot_filename))
    logger.info(f"took screenshot: {os.path.abspath(screenshot_filename)}")


class RAPoller:
    def __init__(self, urls, alert_bot, polling_interval_seconds=5, polling_max_faults=500,
                 alerting_interval_seconds=10, alerting_max_repeats=30):
        self.urls = urls
        self.alert_bot = alert_bot
        self.polling_interval_seconds = polling_interval_seconds
        self.polling_max_faults = polling_max_faults
        self.alerting_interval_seconds = alerting_interval_seconds
        self.alerting_max_repeats = alerting_max_repeats
        self.start_time = datetime.datetime.now()

    def poll(self, driver, url):
        logger.info(f'polling {url}')
        driver.get(url)
        driver.switch_to.frame('#tickets-iframe-m')
        tickets_container = driver.find_element_by_xpath('//*[@id="ticket-types"]/ul')
        ticket_tiers = tickets_container.find_elements_by_tag_name('li')
        for i, tier in enumerate(ticket_tiers):
            status = tier.get_attribute('class')
            if 'onsale' in status or 'closed' not in status:
                tier_type = tier.text.strip().replace('\n', ' ')
                message = f"potential tickets found! (tier={i}, type='{tier_type}', status='{status}', url='{url}')"
                logger.info(message)
                take_screenshot(driver)
                tier.click()
                buy_button = driver.find_element_by_id('buynow')
                buy_button.click()

                alert_repeat_count = 0
                while alert_repeat_count < self.alerting_max_repeats:
                    self.alert_bot.send_message(message + ' Reply with OK to silence alert, or KILL to stop polling.')
                    time.sleep(self.alerting_interval_seconds)
                    update = self.alert_bot.pop_newest()
                    if update:
                        text = update.message.text
                        logger.info(f"received text: {text}")
                        if text.strip().lower() == 'ok':
                            self.urls.pop(self.urls.index(url))
                            self.alert_bot.send_message(f'Removed {url} from ticket monitoring.')
                            return
                        elif text.strip().lower() == 'kill':
                            raise KeyboardInterrupt('Killed by telegram command')

    def run_loop(self, driver):
        fault_count = 0
        while self.urls:
            for url in self.urls:
                try:
                    self.poll(driver, url)
                    update = self.alert_bot.pop_newest()
                    if update:
                        text = update.message.text
                        logger.info(f"received text {text}")
                        if text.strip().lower() == 'status':
                            self.alert_bot.send_message(f'running. monitoring {len(self.urls)} urls. '
                                                        f'uptime: {datetime.datetime.now() - self.start_time}')
                        elif text.strip().lower() == 'kill':
                            raise KeyboardInterrupt('Killed by telegram command')

                except KeyboardInterrupt:
                    return
                except Exception as e:
                    if fault_count >= self.polling_max_faults - 1:
                        message = f"Encountered {fault_count} sequential exceptions: {e}"
                        logger.critical(message)
                        self.alert_bot.send_message(message)
                        raise e
                    take_screenshot(driver)
                    logger.exception(e)
                    fault_count += 1
                else:
                    fault_count = 0
                finally:
                    time.sleep(self.polling_interval_seconds)
                    
                    
def telegram_chat_id_helper(bot_token, sleep_interval=5):
    """ Helper to assist finding your telegram chat id for notifications. """
    bot = telegram.Bot(bot_token)
    safety_code = ''.join([secrets.choice(string.digits) for _ in range(4)])
    logger.info(f'To subscribe to alerts, message "{safety_code}" to '
                f'{bot.name} from your Telegram app now...')
    visited_update_ids = {u.update_id for u in bot.get_updates()}
    while True:
        updates = bot.get_updates()
        if updates:
            latest_update = updates[-1]
            if latest_update.update_id not in visited_update_ids:
                visited_update_ids.add(latest_update.update_id)
                text = latest_update.message.text
                if safety_code in text.strip():
                    chat_id = latest_update.message.from_user.id
                    logger.info(f'Found chat_id {chat_id} for telegram user '
                                f'{latest_update.message.from_user.username}')
                    return chat_id
        time.sleep(sleep_interval)


def main():
    argparser = argparse.ArgumentParser()
    argparser.add_argument('-c', '--config', default='ra_config.json', help='Path to json config.')
    args = argparser.parse_args()

    config = json.load(open(args.config, 'r'))

    telegram_bot_token = config.get('telegram_bot_token')
    if not telegram_bot_token:
        raise RuntimeError(f"telegram_bot_token is required (missing from {args.config}). "
                           f"See how to create a bot: https://core.telegram.org/bots#creating-a-new-bot")

    telegram_chat_id = config.get('telegram_chat_id')
    if not telegram_chat_id:
        telegram_chat_id = telegram_chat_id_helper(telegram_bot_token)
        config['telegram_chat_id'] = telegram_chat_id
        json.dump(config, open(args.config, 'w'), indent=2)
        logger.info(f'Committed telegram_chat_id={telegram_chat_id} to {args.config}')

    alert_bot = TelegramClient(telegram_bot_token, telegram_chat_id, app_name='RA poller')
    alert_bot.pop_newest()  # Clear the queue to prevent stale updates from being parsed.

    poller = RAPoller(config['ticket_urls'], alert_bot)

    profile = webdriver.FirefoxProfile()
    geckodriver_name = 'geckodriver.exe' if platform.system() == 'Windows' else 'geckodriver'
    driver = webdriver.Firefox(firefox_profile=profile,
                               executable_path=os.path.abspath(os.path.join('bin', geckodriver_name)),
                               service_log_path=os.path.abspath(os.path.join('logs', 'geckodriver.log')))

    try:
        alert_bot.send_message("starting up. reply KILL to shut down or STATUS for info.")
        poller.run_loop(driver)
    except Exception as e:
        logger.exception(str(e))
    finally:
        alert_bot.send_message(f"shut down. total runtime: {datetime.datetime.now() - poller.start_time}")
        driver.close()


if __name__ == '__main__':
    main()
