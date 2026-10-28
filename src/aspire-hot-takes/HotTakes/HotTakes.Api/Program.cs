using System.Diagnostics;
using System.Text.Json;
using Npgsql;
using StackExchange.Redis;

var builder = WebApplication.CreateBuilder(args);

builder.AddServiceDefaults();

// Aspire client integrations: connection strings, health checks and tracing are wired up automatically
builder.AddNpgsqlDataSource("hottakesdb");
builder.AddRedisClient("cache");

// The Hot Take Bot's brain: an LLM on the NPU, reached via service discovery ("npu-agent" in the AppHost)
#pragma warning disable EXTEXP0001 // RemoveAllResilienceHandlers is marked experimental
builder.Services.AddHttpClient<NpuAgentClient>(client => client.BaseAddress = new("http://npu-agent"))
    .RemoveAllResilienceHandlers()
    .AddStandardResilienceHandler(options =>
    {
        // LLM calls are slow and not idempotent: allow time, but never retry or trip a breaker
        options.AttemptTimeout.Timeout = TimeSpan.FromSeconds(60);
        options.TotalRequestTimeout.Timeout = TimeSpan.FromSeconds(90);
        options.CircuitBreaker.SamplingDuration = TimeSpan.FromSeconds(120);
        options.Retry.ShouldHandle = _ => ValueTask.FromResult(false);
        options.CircuitBreaker.ShouldHandle = _ => ValueTask.FromResult(false);
    });
#pragma warning restore EXTEXP0001
builder.Services.AddSingleton<BotState>();
builder.Services.AddTransient<HotTakeAgent>();
builder.Services.AddHostedService<HotTakeBot>();

var app = builder.Build();

const string LeaderboardKey = Leaderboards.Key;

await SeedDatabaseAsync(app.Services.GetRequiredService<NpgsqlDataSource>(), app.Logger);

app.MapGet("/", () => "Hot Takes API is running. Try /takes");

app.MapGet("/takes", async (NpgsqlDataSource db, IConnectionMultiplexer redis, ILogger<Program> logger) =>
{
    var stopwatch = Stopwatch.StartNew();
    var cache = redis.GetDatabase();

    var cached = await cache.StringGetAsync(LeaderboardKey);
    if (cached.HasValue)
    {
        var fromCache = JsonSerializer.Deserialize<List<HotTake>>(cached.ToString())!;
        Activity.Current?.SetTag("hottakes.source", "cache");
        logger.LogInformation("Leaderboard served from Redis cache in {ElapsedMs} ms", stopwatch.ElapsedMilliseconds);
        return new Leaderboard("cache", stopwatch.ElapsedMilliseconds, fromCache);
    }

    // Simulates an expensive ranking query so the cache win is easy to see in the trace
    await using var cmd = db.CreateCommand(
        "SELECT id, text, votes, author, device, gen_ms FROM takes, (SELECT pg_sleep(0.3)) AS slow ORDER BY votes DESC, id");
    var takes = new List<HotTake>();
    await using (var reader = await cmd.ExecuteReaderAsync())
    {
        while (await reader.ReadAsync())
        {
            takes.Add(new HotTake(reader.GetInt32(0), reader.GetString(1), reader.GetInt32(2), reader.GetString(3),
                reader.IsDBNull(4) ? null : reader.GetString(4), reader.IsDBNull(5) ? null : reader.GetInt32(5)));
        }
    }

    await cache.StringSetAsync(LeaderboardKey, JsonSerializer.Serialize(takes), TimeSpan.FromSeconds(30));
    Activity.Current?.SetTag("hottakes.source", "database");
    logger.LogInformation("Leaderboard served from Postgres in {ElapsedMs} ms (cache miss)", stopwatch.ElapsedMilliseconds);
    return new Leaderboard("database", stopwatch.ElapsedMilliseconds, takes);
});

app.MapPost("/takes/{id:int}/vote", async (int id, NpgsqlDataSource db, IConnectionMultiplexer redis, ILogger<Program> logger) =>
{
    await using var cmd = db.CreateCommand("UPDATE takes SET votes = votes + 1 WHERE id = $1 RETURNING votes");
    cmd.Parameters.AddWithValue(id);
    var votes = await cmd.ExecuteScalarAsync();
    if (votes is null)
    {
        return Results.NotFound();
    }

    await redis.GetDatabase().KeyDeleteAsync(LeaderboardKey);
    logger.LogInformation("Vote recorded for take {TakeId}; it now has {Votes} votes", id, votes);
    return Results.Ok(new { id, votes });
});

// The Hot Take Bot: an AI agent whose brain runs on the NPU
app.MapGet("/bot", async (BotState state, NpuAgentClient npu, CancellationToken ct) => new
{
    agent = await npu.GetStatusAsync(ct),
    autopilot = state.Autopilot,
    busy = state.Busy.CurrentCount == 0,
    postedThisRun = state.PostedThisRun,
    postedTotal = state.PostedTotal,
    maxPerRun = state.MaxPerRun,
    intervalSeconds = state.IntervalSeconds,
    nextRunInSeconds = state.NextRunAt is { } next ? Math.Max(0, (int)Math.Ceiling((next - DateTimeOffset.UtcNow).TotalSeconds)) : (int?)null,
    last = state.Last,
    lastError = state.LastError,
});

app.MapPost("/bot/autopilot", (bool on, BotState state, ILogger<Program> logger) =>
{
    state.SetAutopilot(on);
    logger.LogInformation("Hot Take Bot autopilot {State}", on ? "ON" : "OFF");
    return Results.Ok(new { autopilot = state.Autopilot });
});

app.MapPost("/bot/generate", async (HotTakeAgent agent, CancellationToken ct) =>
{
    try
    {
        return Results.Ok(await agent.RunCycleAsync("button", ct));
    }
    catch (NpuAgentNotReadyException ex)
    {
        return Results.Problem(ex.Message, statusCode: StatusCodes.Status503ServiceUnavailable);
    }
    catch (BotBusyException ex)
    {
        return Results.Problem(ex.Message, statusCode: StatusCodes.Status409Conflict);
    }
    catch (HttpRequestException ex)
    {
        return Results.Problem($"Could not reach the NPU agent: {ex.Message}", statusCode: StatusCodes.Status503ServiceUnavailable);
    }
});

app.MapDefaultEndpoints();

app.Run();

static async Task SeedDatabaseAsync(NpgsqlDataSource db, ILogger logger)
{
    string[] seedTakes =
    [
        "Tabs are better than spaces",
        "Friday afternoon deploys are fine, actually",
        "Windows is the best place to run Linux containers",
        "Dark mode is overrated",
        "YAML is a programming language now",
        "The best code is the code you delete",
    ];

    for (var attempt = 1; ; attempt++)
    {
        try
        {
            await using var create = db.CreateCommand(
                "CREATE TABLE IF NOT EXISTS takes (id SERIAL PRIMARY KEY, text TEXT NOT NULL UNIQUE, votes INT NOT NULL DEFAULT 0);" +
                "ALTER TABLE takes ADD COLUMN IF NOT EXISTS author TEXT NOT NULL DEFAULT 'human';" +
                "ALTER TABLE takes ADD COLUMN IF NOT EXISTS device TEXT;" +
                "ALTER TABLE takes ADD COLUMN IF NOT EXISTS gen_ms INT;");
            await create.ExecuteNonQueryAsync();

            foreach (var text in seedTakes)
            {
                await using var insert = db.CreateCommand(
                    "INSERT INTO takes (text, votes) VALUES ($1, 0) ON CONFLICT (text) DO NOTHING");
                insert.Parameters.AddWithValue(text);
                await insert.ExecuteNonQueryAsync();
            }

            logger.LogInformation("Database ready with {Count} hot takes", seedTakes.Length);
            return;
        }
        catch (NpgsqlException ex) when (attempt < 10)
        {
            logger.LogWarning(ex, "Database not ready yet (attempt {Attempt}), retrying...", attempt);
            await Task.Delay(TimeSpan.FromSeconds(2));
        }
    }
}

record HotTake(int Id, string Text, int Votes, string Author, string? Device, int? GenMs);

record Leaderboard(string Source, long ElapsedMs, List<HotTake> Takes);
