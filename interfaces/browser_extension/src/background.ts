/**
 * The service worker — the only code that runs with the panel closed.
 *
 * It does one thing: makes the toolbar icon open the side panel.  Chrome
 * will not open a side panel without a user gesture, so "shows up on its
 * own when you open Google Maps" is not something an extension can do;
 * one click, then it stays open across tabs.
 */
chrome.runtime.onInstalled.addListener(() => {
  void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});
