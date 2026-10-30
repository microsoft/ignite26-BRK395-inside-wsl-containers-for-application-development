var builder = WebApplication.CreateBuilder(args);

builder.AddServiceDefaults();

// "api" is resolved by Aspire service discovery - no hardcoded hosts or ports
builder.Services.AddHttpClient("api", client => client.BaseAddress = new("https+http://api"));

// Bot calls wait on the NPU and aren't idempotent, so this client never retries
#pragma warning disable EXTEXP0001 // RemoveAllResilienceHandlers is marked experimental
builder.Services.AddHttpClient("api-bot", client => client.BaseAddress = new("https+http://api"))
    .RemoveAllResilienceHandlers()
    .AddStandardResilienceHandler(options =>
    {
        options.AttemptTimeout.Timeout = TimeSpan.FromSeconds(60);
        options.TotalRequestTimeout.Timeout = TimeSpan.FromSeconds(90);
        options.CircuitBreaker.SamplingDuration = TimeSpan.FromSeconds(120);
        options.Retry.ShouldHandle = _ => ValueTask.FromResult(false);
        options.CircuitBreaker.ShouldHandle = _ => ValueTask.FromResult(false);
    });
#pragma warning restore EXTEXP0001

var app = builder.Build();

app.UseDefaultFiles();
app.UseStaticFiles();

app.MapGet("/leaderboard", async (IHttpClientFactory factory) =>
{
    var response = await factory.CreateClient("api").GetAsync("/takes");
    var body = await response.Content.ReadAsStringAsync();
    return Results.Content(body, "application/json", statusCode: (int)response.StatusCode);
});

app.MapPost("/vote/{id:int}", async (int id, IHttpClientFactory factory) =>
{
    var response = await factory.CreateClient("api").PostAsync($"/takes/{id}/vote", null);
    return Results.StatusCode((int)response.StatusCode);
});

// Hot Take Bot (AI agent on the NPU): status, one-shot generate, and autopilot toggle
app.MapGet("/bot", (IHttpClientFactory factory) => Proxy(factory, HttpMethod.Get, "/bot"));
app.MapPost("/bot/generate", (IHttpClientFactory factory) => Proxy(factory, HttpMethod.Post, "/bot/generate"));
app.MapPost("/bot/autopilot", (bool on, IHttpClientFactory factory) => Proxy(factory, HttpMethod.Post, $"/bot/autopilot?on={on}"));

app.MapDefaultEndpoints();

app.Run();

static async Task<IResult> Proxy(IHttpClientFactory factory, HttpMethod method, string path)
{
    using var response = await factory.CreateClient("api-bot").SendAsync(new HttpRequestMessage(method, path));
    var body = await response.Content.ReadAsStringAsync();
    var contentType = response.Content.Headers.ContentType?.ToString() ?? "application/json";
    return Results.Content(body, contentType, statusCode: (int)response.StatusCode);
}
