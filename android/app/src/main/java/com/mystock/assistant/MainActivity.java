package com.mystock.assistant;

import android.annotation.SuppressLint;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
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
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

public class MainActivity extends AppCompatActivity {
    private WebView webView;
    private SwipeRefreshLayout swipeRefresh;
    private final ExecutorService quoteExecutor = Executors.newFixedThreadPool(6);

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

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

    @Override
    protected void onDestroy() {
        quoteExecutor.shutdownNow();
        super.onDestroy();
    }

    private static String httpGet(String urlStr) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(urlStr).openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(4500);
        conn.setReadTimeout(4500);
        conn.setRequestProperty("User-Agent", "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36");
        conn.setRequestProperty("Accept", "application/json, text/plain, */*");
        int code = conn.getResponseCode();
        if (code < 200 || code >= 300) {
            conn.disconnect();
            throw new Exception("HTTP " + code);
        }
        StringBuilder sb = new StringBuilder();
        try (BufferedReader br = new BufferedReader(new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = br.readLine()) != null) {
                sb.append(line);
            }
        } finally {
            conn.disconnect();
        }
        return sb.toString();
    }

    private static double parseDoubleSafe(String s) {
        if (s == null) return 0.0;
        String clean = s.replace(",", "").replace("%", "").replace("+", "").trim();
        if (clean.isEmpty()) return 0.0;
        try {
            return Double.parseDouble(clean);
        } catch (Exception e) {
            return 0.0;
        }
    }

    private static JSONObject fetchSingleQuote(String symbol) {
        String sym = symbol == null ? "" : symbol.trim();
        if (sym.isEmpty()) return null;
        boolean isKr = sym.endsWith(".KS") || sym.endsWith(".KQ") || sym.matches("^\\d{6}$");
        if (isKr) {
            String code = sym.replaceAll("[^0-9]", "");
            if (code.length() != 6) return null;
            // 1) Naver Mobile Stock Basic API
            try {
                String body = httpGet("https://m.stock.naver.com/api/stock/" + code + "/basic");
                JSONObject obj = new JSONObject(body);
                double price = parseDoubleSafe(obj.optString("closePrice", ""));
                double pct = parseDoubleSafe(obj.optString("fluctuationsRatio", "0"));
                if (price > 0) {
                    JSONObject out = new JSONObject();
                    out.put("price", price);
                    out.put("ret_1d", pct);
                    return out;
                }
            } catch (Exception ignored) {
            }
            // 2) Fallback: Naver Realtime Polling API
            try {
                String body = httpGet("https://polling.finance.naver.com/api/realtime/domestic/stock/" + code);
                JSONObject obj = new JSONObject(body);
                JSONArray datas = obj.optJSONArray("datas");
                if (datas != null && datas.length() > 0) {
                    JSONObject d0 = datas.getJSONObject(0);
                    double price = parseDoubleSafe(d0.optString("closePriceRaw", d0.optString("closePrice", "")));
                    double pct = parseDoubleSafe(d0.optString("fluctuationsRatio", "0"));
                    if (price > 0) {
                        JSONObject out = new JSONObject();
                        out.put("price", price);
                        out.put("ret_1d", pct);
                        return out;
                    }
                }
            } catch (Exception ignored) {
            }
            return null;
        } else {
            // US stock via Yahoo Finance chart API
            try {
                String body = httpGet("https://query1.finance.yahoo.com/v8/finance/chart/" + Uri.encode(sym) + "?interval=1d&range=2d");
                JSONObject obj = new JSONObject(body);
                JSONObject chart = obj.optJSONObject("chart");
                JSONArray res = chart != null ? chart.optJSONArray("result") : null;
                if (res != null && res.length() > 0) {
                    JSONObject meta = res.getJSONObject(0).optJSONObject("meta");
                    if (meta != null) {
                        double price = meta.optDouble("regularMarketPrice", 0.0);
                        double prev = meta.optDouble("chartPreviousClose", meta.optDouble("previousClose", 0.0));
                        double pct = (prev > 0 && price > 0) ? ((price / prev) - 1.0) * 100.0 : 0.0;
                        if (price > 0) {
                            JSONObject out = new JSONObject();
                            out.put("price", price);
                            out.put("ret_1d", Math.round(pct * 100.0) / 100.0);
                            return out;
                        }
                    }
                }
            } catch (Exception ignored) {
            }
            return null;
        }
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
                            JSONObject q = fetchSingleQuote(sym);
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
