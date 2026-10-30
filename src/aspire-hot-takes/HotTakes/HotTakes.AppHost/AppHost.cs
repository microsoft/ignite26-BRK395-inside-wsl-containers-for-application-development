using Microsoft.Extensions.Configuration;

var builder = DistributedApplication.CreateBuilder(args);

// Infrastructure: real Linux containers, running on WSLc (no Docker Desktop)
var postgres = builder.AddPostgres("postgres");
var db = postgres.AddDatabase("hottakesdb");

var cache = builder.AddRedis("cache");

// Our code: a backend API and a web frontend
var api = builder.AddDotnetProject("api", "../HotTakes.Api/HotTakes.Api.csproj")
    .WithReference(db).WaitFor(db)
    .WithReference(cache).WaitFor(cache)
    .WithHttpHealthCheck("/health");

// AI on the NPU: an LLM served from a Linux container, built from a Dockerfile and run on WSLc.
// "--gpus all" passes the GPU *and* the Intel NPU through to the container.
var npuEnabled = builder.Configuration.GetValue("NpuAgent:Enabled", true);
api.WithEnvironment("NpuAgent__Enabled", npuEnabled.ToString());
if (npuEnabled)
{
    var npuModel = builder.Configuration["NpuAgent:Model"] ?? "Qwen3-8B-int4-cw-ov";
    var modelsPath = Path.GetFullPath(builder.Configuration["NpuAgent:ModelsPath"] ?? "../models", builder.AppHostDirectory);

    var npuAgent = builder.AddDockerfile("npu-agent", "../HotTakes.NpuAgent")
        .WithContainerRuntimeArgs("--gpus", "all")
        .WithBindMount(modelsPath, "/models")
        .WithEnvironment("MODEL", npuModel)
        .WithHttpEndpoint(targetPort: 8080, name: "http")
        .WithHttpHealthCheck("/health");

    // No WaitFor: the app starts immediately and the bot comes online once the model has loaded.
    api.WithReference(npuAgent.GetEndpoint("http"));
}

builder.AddDotnetProject("web", "../HotTakes.Web/HotTakes.Web.csproj")
    .WithReference(api).WaitFor(api)
    .WithExternalHttpEndpoints();

builder.Build().Run();
