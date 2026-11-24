package com.mystock.assistant;

import android.Manifest;
import android.annotation.SuppressLint;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import androidx.activity.OnBackPressedCallback;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;
import androidx.work.Constraints;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

public class MainActivity extends AppCompatActivity {
    private static final int REQ_POST_NOTIF = 1001;
    private static final String WORK_NAME = "mystock_periodic_price_alerts";
    private WebView webView;
    private SwipeRefreshLayout swipeRefresh;
    private final ExecutorService quoteExecutor = Executors.newFixedThreadPool(6);

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        StockAlertWorker.ensureChannel(this);
        ensureNotificationPermission();
        scheduleBackgroundAlerts();

        swipeRefresh = new SwipeRefreshLayout(this);
        swipeRefresh.setLayoutParams(new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));
        swipeRefresh.setColorSchemeColors(Color.parseColor("#2563EB"));

        webView = new WebView(this);
        webView.setLayoutParams(new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);

        webView.addJavascriptInterface(new StockQuoteBridge(), "AndroidStockBridge");
        webView.setWebChromeClient(new WebChromeClient());
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String host = uri.getHost() != null ? uri.getHost() : "";
                if (host.endsWith("github.io") || host.endsWith("githubusercontent.com")) {
                    return false;
                }
                Intent intent = new Intent(Intent.ACTION_VIEW, uri);
                startActivity(intent);
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                swipeRefresh.setRefreshing(false);
            }
        });

        swipeRefresh.addView(webView);
        swipeRefresh.setOnRefreshListener(() -> {
            webView.clearCache(true);
            webView.evaluateJavascript(
                    "if(window.triggerAppRefresh){window.triggerAppRefresh();}else{location.reload();}",
                    null
            );
            swipeRefresh.postDelayed(() -> swipeRefresh.setRefreshing(false), 1800);
        });
        setContentView(swipeRefresh);

        webView.loadUrl(BuildConfig.APP_URL + "?v=" + System.currentTimeMillis());

        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack();
                } else {
                    finish();
                }
            }
        });
    }

    private void ensureNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                    != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(
                        this,
                        new String[]{Manifest.permission.POST_NOTIFICATIONS},
                        REQ_POST_NOTIF
                );
            }
        }
    }

    private void scheduleBackgroundAlerts() {
        try {
            Constraints constraints = new Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build();
            PeriodicWorkRequest req = new PeriodicWorkRequest.Builder(
                    StockAlertWorker.class, 15, TimeUnit.MINUTES
            ).setConstraints(constraints).build();
            WorkManager.getInstance(this).enqueueUniquePeriodicWork(
                    WORK_NAME,
                    ExistingPeriodicWorkPolicy.KEEP,
                    req
            );
        } catch (Exception ignored) {
        }
    }

    @Override
    protected void onDestroy() {
        quoteExecutor.shutdownNow();
        super.onDestroy();
    }

    private class StockQuoteBridge {
        @JavascriptInterface
        public String getAppVersion() {
            return BuildConfig.VERSION_NAME;
        }

        @JavascriptInterface
        public void notifyRefreshDone() {
            runOnUiThread(() -> swipeRefresh.setRefreshing(false));
        }

        @JavascriptInterface
        public void requestNotificationPermission() {
            runOnUiThread(MainActivity.this::ensureNotificationPermission);
        }

        @JavascriptInterface
        public void sendNotification(final String title, final String body, final int notifyId) {
            runOnUiThread(() -> StockAlertWorker.postNotification(MainActivity.this, title, body, notifyId));
        }

        @JavascriptInterface
        public void syncAlertRules(final String rulesJson) {
            try {
                SharedPreferences prefs = getSharedPreferences(StockAlertWorker.PREF_NAME, MODE_PRIVATE);
                prefs.edit().putString(StockAlertWorker.KEY_RULES, rulesJson != null ? rulesJson : "").apply();
                scheduleBackgroundAlerts();
            } catch (Exception ignored) {
            }
        }

        @JavascriptInterface
        public void fetchLiveQuotes(final String symbolsCsv) {
            new Thread(() -> {
                JSONObject result = new JSONObject();
                try {
                    Set<String> unique = new LinkedHashSet<>();
                    if (symbolsCsv != null) {
                        for (String raw : symbolsCsv.split(",")) {
                            String s = raw.trim();
                            if (!s.isEmpty()) unique.add(s);
                        }
                    }
                    List<Future<String[]>> futures = new ArrayList<>();
                    for (final String sym : unique) {
                        Callable<String[]> task = () -> {
                            JSONObject q = QuoteFetcher.fetchSingleQuote(sym);
                            return q != null ? new String[]{sym, q.toString()} : null;
                        };
                        futures.add(quoteExecutor.submit(task));
                    }
                    for (Future<String[]> f : futures) {
                        try {
                            String[] pair = f.get();
                            if (pair != null) {
                                result.put(pair[0], new JSONObject(pair[1]));
                            }
                        } catch (Exception ignored) {
                        }
                    }
                } catch (Exception ignored) {
                }
                final String payload = result.toString();
                runOnUiThread(() -> {
                    swipeRefresh.setRefreshing(false);
                    webView.evaluateJavascript(
                            "window.onLiveQuotesUpdated && window.onLiveQuotesUpdated(" + payload + ");",
                            null
                    );
                });
            }).start();
        }
    }
}
